import logging
import uuid
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import AsyncSessionLocal
from backend.app.core.security import verify_token
from backend.app.services.voice_service import voice_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/stream")
async def websocket_voice_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
):
    """Realtime WebSocket endpoint for streaming audio and receiving transcriptions.
    
    Accepts:
        - Query param 'token' for credentials.
    Receives:
        - JSON control messages (e.g., {"type": "start", "payload": {"patient_id": 1}})
        - Binary audio chunks (raw 16kHz 16-bit mono PCM).
    Sends:
        - JSON server events (e.g., {"event": "transcript_diff", "payload": {...}})
    """
    # 1. Authenticate user from JWT token
    user_id_str = verify_token(token)
    if not user_id_str:
        logger.warning("Rejected WebSocket connection: Invalid credentials token.")
        await websocket.close(code=4008)  # Policy violation
        return

    creator_id = int(user_id_str)
    session_id = str(uuid.uuid4())
    
    await websocket.accept()
    await voice_service.register_connection(session_id, websocket)

    # Send initial connection confirmation event
    await websocket.send_json({
        "event": "connected",
        "session_id": session_id,
        "payload": {"message": "Voice WebSocket server connection established."}
    })

    # Start database session for this long-lived WebSocket lifecycle
    async with AsyncSessionLocal() as db:
        try:
            while True:
                # Receive message
                message = await websocket.receive()
                
                # Handle binary audio frame
                if "bytes" in message:
                    audio_data = message["bytes"]
                    await voice_service.process_audio_chunk(session_id, audio_data)
                    
                # Handle text control message (JSON format)
                elif "text" in message:
                    import json
                    try:
                        data = json.loads(message["text"])
                        msg_type = data.get("type")
                        payload = data.get("payload", {})
                        
                        if msg_type == "start":
                            patient_id = payload.get("patient_id")
                            if not patient_id:
                                await websocket.send_json({
                                    "event": "error",
                                    "session_id": session_id,
                                    "payload": {"message": "Missing 'patient_id' in start payload"}
                                })
                                continue
                                
                            await voice_service.start_session(
                                session_id=session_id,
                                patient_id=int(patient_id),
                                creator_id=creator_id
                            )
                            await websocket.send_json({
                                "event": "started",
                                "session_id": session_id,
                                "payload": {"patient_id": patient_id}
                            })
                            
                        elif msg_type == "stop":
                            # Finalize session: generate SOAP note and write to DB
                            db_transcript = await voice_service.finalize_session(session_id, db)
                            logger.info(f"Session {session_id} finalized. Record ID: {db_transcript.id}")
                            
                            # Break loop and close connection since session is finished
                            break
                            
                        else:
                            await websocket.send_json({
                                "event": "error",
                                "session_id": session_id,
                                "payload": {"message": f"Unsupported event type: {msg_type}"}
                            })
                    except json.JSONDecodeError:
                        await websocket.send_json({
                            "event": "error",
                            "session_id": session_id,
                            "payload": {"message": "Invalid JSON text message"}
                        })
                    except Exception as e:
                        logger.error(f"Error handling WebSocket message: {e}", exc_info=True)
                        await websocket.send_json({
                            "event": "error",
                            "session_id": session_id,
                            "payload": {"message": f"Server error: {str(e)}"}
                        })
                        
        except WebSocketDisconnect:
            logger.info(f"WebSocket client disconnected for session: {session_id}")
        except Exception as e:
            logger.error(f"WebSocket crash in session {session_id}: {e}", exc_info=True)
        finally:
            # Cleanup resources
            await voice_service.unregister_connection(session_id)
            try:
                await websocket.close()
            except RuntimeError:
                # Already closed
                pass
