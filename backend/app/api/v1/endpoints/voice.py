"""
voice.py
--------
Voice API: HTTP voice chat + WebSocket realtime pipeline.

Routes:
  POST /api/v1/voice/chat   — upload audio → STT → Groq → TTS
  WS   /api/v1/voice/stream — text, voice audio, reasoning traces
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, Query

from app.core.database import AsyncSessionLocal
from app.schemas.voice import VoiceChatResponse
from app.services.campaign_service import campaign_service, normalize_campaign_type
from app.services.connection_manager import manager
from app.services.ai_service import ai_service
from app.services.voice_service import voice_service
from app.services.memory_manager import bind_patient_to_session, clear_session_cache
from app.services.ws_pipeline import emit_assistant_response, process_turn_with_timeout
from app.services.ws_session import (
    cancel_active_turn,
    cleanup_session,
    clear_turn_task,
    get_generation,
    register_turn_task,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Per-session binary audio buffers (websocket PCM/webm chunks)
_audio_buffers: Dict[str, bytearray] = {}
_patient_ids: Dict[str, int] = {}


async def _emit_trace(websocket: WebSocket, session_id: str, event: dict) -> None:
    await websocket.send_json({
        "event": "reasoning_trace",
        "type": "reasoning_trace",
        "session_id": session_id,
        "payload": event,
    })


async def _run_pipeline_turn(
    websocket: WebSocket,
    session_id: str,
    *,
    user_text: Optional[str] = None,
    audio_bytes: Optional[bytes] = None,
    content_type: str = "audio/webm",
    language_hint: str = "auto",
    cancel_reason: str = "new_input",
) -> None:
    """Cancel any in-flight turn and start a new one."""
    await cancel_active_turn(session_id, reason=cancel_reason)
    generation = get_generation(session_id)

    async def _trace_cb(event: dict):
        await _emit_trace(websocket, session_id, event)

    if user_text and session_id in _patient_ids:
        async with AsyncSessionLocal() as db:
            await campaign_service.record_patient_response(
                db,
                session_id=session_id,
                user_text=user_text,
                trace_callback=_trace_cb,
            )

    task = asyncio.create_task(
        process_turn_with_timeout(
            websocket,
            session_id,
            user_text=user_text,
            audio_bytes=audio_bytes,
            content_type=content_type,
            language_hint=language_hint,
            generation=generation,
        )
    )
    register_turn_task(session_id, task)
    try:
        await task
    except asyncio.CancelledError:
        logger.info("[WS] Pipeline task cancelled | session=%s", session_id)
    finally:
        clear_turn_task(session_id, task)


@router.post("/chat", response_model=VoiceChatResponse)
async def voice_chat(
    audio: UploadFile = File(..., description="Recorded audio (webm, wav, mp4, mpeg)"),
    session_id: str | None = Form(None, description="Optional session ID for conversation memory"),
    language: str = Form("auto", description="Language hint: auto, en, hi, ta"),
):
    """HTTP voice chat — same pipeline as WS voice_end."""
    logger.info("[VOICE/CHAT] Incoming audio | filename=%s | session=%s", audio.filename, session_id)

    try:
        audio_bytes = await audio.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read audio: {exc}") from exc

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    content_type = audio.content_type or "audio/webm"
    if audio.filename and audio.filename.endswith(".wav"):
        content_type = "audio/wav"

    result = await voice_service.process_voice_chat(
        audio_bytes,
        session_id=session_id,
        content_type=content_type,
        language_hint=language,
    )

    if not result.get("success") and not result.get("transcript"):
        raise HTTPException(
            status_code=422,
            detail=result.get("ai_response", "Voice processing failed."),
        )

    logger.info(
        "[VOICE/CHAT] Done | session=%s | STT=%.0fms LLM=%.0fms TTS=%.0fms TOTAL=%.0fms",
        result["session_id"],
        result["metrics"].get("stt_latency_ms", 0),
        result["metrics"].get("llm_latency_ms", 0),
        result["metrics"].get("tts_latency_ms", 0),
        result["metrics"].get("total_latency_ms", 0),
    )

    return VoiceChatResponse(
        session_id=result["session_id"],
        transcript=result["transcript"],
        detected_language=result["detected_language"],
        ai_response=result["ai_response"],
        audio_base64=result["audio_base64"],
        audio_mime_type=result["audio_mime_type"],
        reasoning_traces=result.get("reasoning_traces", []),
        metrics=result["metrics"],
    )


@router.websocket("/stream")
async def websocket_voice_endpoint(
    websocket: WebSocket,
    token: str = Query(None),
):
    """
    Realtime WebSocket:
      IN  type=start | text | voice_audio | voice_end | stop
      OUT type=assistant_response | event=chat_response (legacy) | reasoning_trace
    """
    session_id = str(uuid.uuid4())
    logger.info("[WS] Incoming connection | session=%s", session_id)

    try:
        await manager.connect(session_id, websocket)
    except Exception as e:
        logger.error("[WS] Failed to accept | session=%s | %s", session_id, e)
        return

    ai_service.register_session(session_id)
    _audio_buffers[session_id] = bytearray()
    source_language = "auto"

    try:
        while True:
            message = await websocket.receive()

            # ── Binary audio chunks (legacy streaming) ───────────────────
            if "bytes" in message:
                chunk = message["bytes"]
                _audio_buffers.setdefault(session_id, bytearray()).extend(chunk)
                logger.debug("[WS IN] audio chunk | session=%s | +%d bytes", session_id, len(chunk))
                continue

            if "text" not in message:
                continue

            try:
                data = json.loads(message["text"])
            except json.JSONDecodeError:
                logger.warning("[WS] Non-JSON message | session=%s", session_id)
                continue

            msg_type = data.get("type")
            payload = data.get("payload") or {}
            logger.info("[WS IN] type=%s | session=%s", msg_type, session_id)

            if msg_type == "barge_in":
                logger.info("[BARGE IN] session=%s", session_id)
                await cancel_active_turn(session_id, reason="barge_in")
                await websocket.send_json({
                    "type": "barge_in_ack",
                    "event": "barge_in_ack",
                    "session_id": session_id,
                    "payload": {"status": "cancelled", "message": "TTS and pending response cancelled"},
                })
                await _emit_trace(
                    websocket,
                    session_id,
                    {
                        "node": "barge_in",
                        "timestamp": time.time(),
                        "tool_result": "[BARGE IN] User interrupted assistant",
                    },
                )
                await _emit_trace(
                    websocket,
                    session_id,
                    {
                        "node": "tts_cancelled",
                        "timestamp": time.time(),
                        "tool_result": "[TTS CANCELLED] Playback should stop on client",
                    },
                )
                await _emit_trace(
                    websocket,
                    session_id,
                    {
                        "node": "state_updated",
                        "timestamp": time.time(),
                        "tool_result": "[STATE UPDATED] Ready for new user input",
                    },
                )
                follow_text = (payload.get("text") or "").strip()
                if follow_text:
                    await _run_pipeline_turn(
                        websocket,
                        session_id,
                        user_text=follow_text,
                        language_hint=source_language,
                        cancel_reason="barge_in_followup",
                    )
                continue

            if msg_type == "start":
                source_language = payload.get("source_language", "auto")
                _audio_buffers[session_id] = bytearray()
                patient_id = payload.get("patient_id")
                if patient_id is not None:
                    try:
                        pid = int(patient_id)
                        _patient_ids[session_id] = pid
                        await bind_patient_to_session(session_id, pid)
                    except (TypeError, ValueError) as exc:
                        logger.warning("[WS] Invalid patient_id: %s", exc)
                await websocket.send_json({
                    "event": "started",
                    "type": "started",
                    "session_id": session_id,
                    "payload": {
                        "patient_id": payload.get("patient_id"),
                        "source_language": source_language,
                    },
                })

            elif msg_type == "text":
                user_text = (payload.get("text") or "").strip()
                if not user_text:
                    continue
                await _run_pipeline_turn(
                    websocket,
                    session_id,
                    user_text=user_text,
                    language_hint=source_language if source_language != "auto" else "en",
                )

            elif msg_type in ("voice_audio", "voice_end"):
                # Full audio blob in JSON (preferred for MediaRecorder)
                audio_b64 = payload.get("audio_base64") or payload.get("audio_data")
                mime = payload.get("mime_type") or payload.get("content_type") or "audio/webm"
                lang = payload.get("language") or source_language

                if audio_b64:
                    audio_bytes = base64.b64decode(audio_b64)
                elif msg_type == "voice_end" and _audio_buffers.get(session_id):
                    audio_bytes = bytes(_audio_buffers[session_id])
                    _audio_buffers[session_id] = bytearray()
                else:
                    await emit_assistant_response(
                        websocket,
                        session_id,
                        text="No audio received. Please try recording again.",
                        error="no_audio",
                    )
                    continue

                await _run_pipeline_turn(
                    websocket,
                    session_id,
                    audio_bytes=audio_bytes,
                    content_type=mime,
                    language_hint=lang,
                )

            elif msg_type == "trigger_outbound":
                campaign_type = normalize_campaign_type(
                    payload.get("campaign_type", "reminder")
                )
                patient_id = payload.get("patient_id") or _patient_ids.get(session_id, 1)
                try:
                    patient_id = int(patient_id)
                except (TypeError, ValueError):
                    patient_id = 1
                _patient_ids[session_id] = patient_id

                async def _campaign_trace(event: dict):
                    await _emit_trace(websocket, session_id, event)

                async with AsyncSessionLocal() as db:
                    outbound = await campaign_service.build_outbound_turn(
                        db,
                        session_id=session_id,
                        patient_id=patient_id,
                        campaign_type=campaign_type,
                        appointment_id=payload.get("appointment_id"),
                        preferred_language=payload.get("preferred_language"),
                        message_template=payload.get("message_template"),
                        campaign_log_id=payload.get("campaign_log_id"),
                        trace_callback=_campaign_trace,
                    )
                await _emit_trace(
                    websocket,
                    session_id,
                    {
                        "node": "campaign_triggered",
                        "timestamp": time.time(),
                        "tool_result": f"[CAMPAIGN START] {outbound['campaign_type']}",
                        "campaign_type": outbound["campaign_type"],
                    },
                )
                await _run_pipeline_turn(
                    websocket,
                    session_id,
                    user_text=outbound["instruction"],
                    language_hint=outbound.get("language", "en"),
                    cancel_reason="campaign_start",
                )

            elif msg_type == "stop":
                logger.info("[WS] Stop | session=%s", session_id)
                break

            else:
                await websocket.send_json({
                    "event": "error",
                    "type": "error",
                    "session_id": session_id,
                    "payload": {"message": f"Unknown type: {msg_type}"},
                })

    except WebSocketDisconnect:
        logger.info("[WS] Disconnected | session=%s", session_id)
    except Exception as e:
        logger.error("[WS] Error | session=%s | %s", session_id, e, exc_info=True)
        try:
            await emit_assistant_response(
                websocket,
                session_id,
                text="An internal error occurred. Please try again.",
                error=str(e),
            )
        except Exception:
            pass
    finally:
        await cancel_active_turn(session_id, reason="disconnect")
        _audio_buffers.pop(session_id, None)
        _patient_ids.pop(session_id, None)
        cleanup_session(session_id)
        manager.disconnect(session_id)
        ai_service.unregister_session(session_id)
        clear_session_cache(session_id)
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("[WS] Cleanup | session=%s", session_id)
