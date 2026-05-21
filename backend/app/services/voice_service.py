import logging
import asyncio
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
        self.is_active = True
        self.audio_chunks: List[bytes] = []
        self.transcript_history: List[str] = []
        self.translation_history: List[str] = []
        self.detected_languages: List[str] = []
        self.chat_history: List[Dict[str, str]] = []


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
        self, session_id: str, patient_id: int, creator_id: int
    ) -> VoiceSessionState:
        """Initializes state for a new audio streaming session."""
        state = VoiceSessionState(session_id, patient_id, creator_id)
        self.active_sessions[session_id] = state
        logger.info(f"Started voice session state for {session_id} (Patient: {patient_id})")
        return state

    async def process_audio_chunk(self, session_id: str, chunk: bytes):
        """Processes incoming audio packet.
        
        1. Accumulates binary audio.
        2. Calls STT service to get transcript.
        3. Broadcasts intermediate transcription back to patient/clinician.
        """
        state = self.active_sessions.get(session_id)
        websocket = self.active_sockets.get(session_id)
        
        if not state or not websocket:
            logger.error(f"Cannot process audio: Session {session_id} is inactive or missing socket.")
            return

        # 1. Accumulate audio bytes
        state.audio_chunks.append(chunk)

        try:
            # 2. Transcribe chunk (multilingual logic inside STT)
            stt_result = await self.stt.transcribe_chunk(
                chunk, 
                sample_rate=settings.AUDIO_SAMPLE_RATE,
                language="auto"
            )
            
            transcript = stt_result.get("transcript", "")
            lang = stt_result.get("language", "en")
            
            if transcript:
                # Store history
                state.transcript_history.append(transcript)
                if lang not in state.detected_languages:
                    state.detected_languages.append(lang)
                
                # In production: Translate transcript if it's not the clinician's language
                translated = await self.llm.translate_text(transcript, source_language=lang, target_language="en")
                state.translation_history.append(translated)

                # 3. Broadcast real-time transcription to client
                await websocket.send_json({
                    "event": "transcript_diff",
                    "session_id": session_id,
                    "payload": {
                        "original_text": transcript,
                        "translated_text": translated,
                        "language": lang,
                        "is_final": stt_result.get("is_final", False)
                    }
                })
        except Exception as e:
            logger.error(f"Error processing audio in session {session_id}: {e}", exc_info=True)
            await websocket.send_json({
                "event": "error",
                "session_id": session_id,
                "payload": {"message": f"Error transcribing audio: {str(e)}"}
            })

    async def finalize_session(self, session_id: str, db: AsyncSession) -> Transcript:
        """Ends streaming, generates SOAP clinical notes, and saves to database."""
        state = self.active_sessions.get(session_id)
        websocket = self.active_sockets.get(session_id)

        if not state:
            raise ValueError(f"Session {session_id} not found.")

        logger.info(f"Finalizing session: {session_id}. Compiling documentation...")

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
