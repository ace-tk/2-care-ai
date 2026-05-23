import logging
import asyncio
import time
import base64
from typing import Dict, Any, List
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.services.stt_service import STTService
from backend.app.services.tts_service import TTSService
from backend.app.services.llm_service import LLMService
from backend.app.models.transcript import Transcript
from backend.app.models.patient import Patient

logger = logging.getLogger(__name__)


class VoiceSessionState:
    """Represents the in-memory state of an active real-time voice session."""

    def __init__(self, session_id: str, patient_id: int, creator_id: int):
        self.session_id = session_id
        self.patient_id = patient_id
        self.creator_id = creator_id
        self.session_language = "en"
        self.is_active = True
        self.audio_chunks: List[bytes] = []
        self.transcript_history: List[str] = []
        self.translation_history: List[str] = []
        self.detected_languages: List[str] = []
        self.chat_history: List[Dict[str, str]] = []
        self.dg_connection = None


class VoiceService:
    """Orchestrator service for real-time multilingual voice consultations."""

    def __init__(self):
        # Initialize sub-services with API keys from configurations
        self.stt = STTService(api_key=settings.DEEPGRAM_API_KEY)
        self.tts = TTSService(api_key=settings.ELEVENLABS_API_KEY)
        self.llm = LLMService(api_key=settings.OPENAI_API_KEY)
        
        # Track active in-memory sessions: session_id -> VoiceSessionState
        self.active_sessions: Dict[str, VoiceSessionState] = {}
        # Track active web sockets: session_id -> WebSocket
        self.active_sockets: Dict[str, WebSocket] = {}

    async def register_connection(self, session_id: str, websocket: WebSocket):
        """Register client websocket connection."""
        self.active_sockets[session_id] = websocket
        logger.info(f"Registered WebSocket connection for session: {session_id}")

    async def unregister_connection(self, session_id: str):
        """Remove and clean up connection."""
        if session_id in self.active_sockets:
            del self.active_sockets[session_id]
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
        logger.info(f"Unregistered session: {session_id}")

    async def start_session(
        self, session_id: str, patient_id: int, creator_id: int, source_language: str = "en"
    ) -> VoiceSessionState:
        """Initializes state for a new audio streaming session and connects to Deepgram STT."""
        state = VoiceSessionState(session_id, patient_id, creator_id)
        state.session_language = source_language
        self.active_sessions[session_id] = state
        
        # Define STT callback
        async def on_transcript(data: dict):
            websocket = self.active_sockets.get(session_id)
            if not websocket:
                return
                
            transcript = data.get("transcript", "")
            lang = data.get("language", "en")
            
            if transcript:
                state.transcript_history.append(transcript)
                
                # If language is auto or not detected yet, try to detect from text
                if state.session_language == "auto" and data.get("is_final", False):
                    detected = await self.llm.detect_language(transcript)
                    if detected:
                        state.session_language = detected
                        lang = detected
                else:
                    lang = state.session_language if state.session_language != "auto" else "en"
                    
                if lang not in state.detected_languages:
                    state.detected_languages.append(lang)
                    
                # Translate to English for internal system processing
                translated = transcript
                if lang != "en" and data.get("is_final", False):
                    translated = await self.llm.translate_text(transcript, source_language=lang, target_language="en")
                    state.translation_history.append(translated)
                
                # Broadcast real-time transcription to client
                try:
                    await websocket.send_json({
                        "event": "transcript_diff",
                        "session_id": session_id,
                        "payload": {
                            "original_text": transcript,
                            "translated_text": translated,
                            "language": lang,
                            "is_final": data.get("is_final", False)
                        }
                    })
                except Exception as e:
                    logger.error(f"Error broadcasting transcript for {session_id}: {e}")

        # Connect to Deepgram
        try:
            # Map "auto" or empty to "en" for Deepgram options
            lang_code = source_language if source_language and source_language != "auto" else "en"
            dg_conn = await self.stt.create_streaming_session(
                on_transcript=on_transcript,
                sample_rate=settings.AUDIO_SAMPLE_RATE,
                language=lang_code
            )
            state.dg_connection = dg_conn
        except Exception as e:
            logger.error(f"Failed to initialize STT for session {session_id}: {e}")
            
        logger.info(f"Started voice session state for {session_id} (Patient: {patient_id})")
        return state

    async def process_audio_chunk(self, session_id: str, chunk: bytes, recv_time: float = None):
        """Processes incoming audio packet.
        
        1. Accumulates binary audio.
        2. Logs chunk metrics and transmission timing.
        """
        state = self.active_sessions.get(session_id)
        websocket = self.active_sockets.get(session_id)
        
        if not state or not websocket:
            logger.error(f"Cannot process audio: Session {session_id} is inactive or missing socket.")
            return

        # 1. Accumulate audio bytes
        state.audio_chunks.append(chunk)

        # 2. Log audio chunk metadata
        chunk_size = len(chunk)
        process_time = time.time()
        timing_ms = (process_time - recv_time) * 1000 if recv_time else 0.0
        
        logger.info(
            f"[AUDIO_PIPELINE] Session {session_id[:8]} | "
            f"Chunk Size: {chunk_size:4d} bytes | "
            f"Queue Latency: {timing_ms:.2f} ms | "
            f"Total Chunks: {len(state.audio_chunks)}"
        )

        # 3. Stream to Deepgram STT
        if state.dg_connection:
            stt_start_time = time.time()
            try:
                await state.dg_connection.send(chunk)
                stt_latency = (time.time() - stt_start_time) * 1000
                logger.debug(f"[STT_STREAM] Sent chunk to Deepgram (Send Latency: {stt_latency:.2f} ms)")
            except Exception as e:
                logger.error(f"Deepgram send error for session {session_id}: {e}")

    async def process_chat_message(self, session_id: str, text: str):
        """Processes an incoming text chat message, appending to history and generating an LLM response."""
        state = self.active_sessions.get(session_id)
        websocket = self.active_sockets.get(session_id)
        
        if not state or not websocket:
            logger.error(f"Cannot process chat: Session {session_id} is inactive or missing socket.")
            return
            
        # Append user message to state
        state.chat_history.append({"sender": "user", "text": text})
        
        # Detect language if auto
        if state.session_language == "auto":
            state.session_language = await self.llm.detect_language(text)
            
        # Generate conversational response using OpenAI Orchestrator
        try:
            # We pass the new prompt and the history excluding the new prompt itself
            ai_response = await self.llm.generate_response(
                prompt=text, 
                session_id=session_id,
                history=state.chat_history[:-1],
                language=state.session_language
            )
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            ai_response = "I apologize, but I am currently unable to process your request."
            
        # Append AI response to state
        state.chat_history.append({"sender": "ai", "text": ai_response})
        
        # Send back to client
        try:
            await websocket.send_json({
                "event": "chat_response",
                "session_id": session_id,
                "payload": {"text": ai_response, "sender": "ai"}
            })
        except Exception as e:
            logger.error(f"Failed to send chat response to {session_id}: {e}")
            
        # Stream TTS audio
        async def text_iterator():
            # Yield chunks to simulate stream or pass directly
            words = ai_response.split(" ")
            for i in range(0, len(words), 5):
                chunk = " ".join(words[i:i+5]) + " "
                yield chunk
                await asyncio.sleep(0.01)
                
        try:
            async for audio_chunk in self.tts.stream_speech(text_iterator()):
                b64_audio = base64.b64encode(audio_chunk).decode('utf-8')
                await websocket.send_json({
                    "event": "audio_stream",
                    "session_id": session_id,
                    "payload": {
                        "audio_data": b64_audio
                    }
                })
        except Exception as e:
            logger.error(f"Error streaming TTS audio to client: {e}")

    async def finalize_session(self, session_id: str, db: AsyncSession) -> Transcript:
        """Ends streaming, generates SOAP clinical notes, and saves to database."""
        state = self.active_sessions.get(session_id)
        websocket = self.active_sockets.get(session_id)

        if not state:
            raise ValueError(f"Session {session_id} not found.")

        logger.info(f"Finalizing session: {session_id}. Compiling documentation...")

        # 1. Stop Deepgram streaming
        if state.dg_connection:
            try:
                await state.dg_connection.finish()
            except Exception as e:
                logger.error(f"Error finishing Deepgram stream: {e}")

        # 1. Get full conversations
        full_original = " ".join(state.transcript_history)
        full_translated = " ".join(state.translation_history)
        detected_lang = state.detected_languages[0] if state.detected_languages else "en"

        # Fetch patient details for context
        patient = await db.get(Patient, state.patient_id)
        patient_info = {
            "id": patient.id if patient else state.patient_id,
            "first_name": patient.first_name if patient else "Unknown",
            "last_name": patient.last_name if patient else "Patient",
        }

        # 2. Generate SOAP note
        clinical_summary = await self.llm.generate_clinical_summary(
            transcript=full_translated or full_original, 
            patient_info=patient_info
        )

        # 3. Save to database
        db_transcript = Transcript(
            patient_id=state.patient_id,
            creator_id=state.creator_id,
            session_id=session_id,
            audio_url=None,  # In production: upload state.audio_chunks to S3/GCS and save URL
            detected_language=detected_lang,
            original_text=full_original,
            translated_text=full_translated,
            clinical_summary=clinical_summary,
        )
        
        db.add(db_transcript)
        await db.commit()
        await db.refresh(db_transcript)

        # 4. Notify client of completion
        if websocket:
            await websocket.send_json({
                "event": "summary_completed",
                "session_id": session_id,
                "payload": {
                    "transcript_id": db_transcript.id,
                    "clinical_summary": clinical_summary,
                }
            })

        # 5. Clean up in-memory sessions
        await self.unregister_connection(session_id)
        
        return db_transcript


voice_service = VoiceService()
