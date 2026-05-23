"""
voice_service.py
----------------
Orchestrates the multilingual voice pipeline:

  Audio upload → Deepgram STT → Groq agent (tools + memory) → ElevenLabs TTS → response

Reuses ai_service / groq_service for booking, cancel, and reschedule flows.
"""

import base64
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from app.services.ai_service import ai_service
from app.services.stt_service import get_stt_service
from app.services.tts_service import synthesize_speech_safe

logger = logging.getLogger(__name__)


class VoiceService:
    """HTTP voice chat orchestrator (STT → LLM → TTS)."""

    async def process_voice_chat(
        self,
        audio_bytes: bytes,
        *,
        session_id: Optional[str] = None,
        content_type: str = "audio/webm",
        language_hint: str = "auto",
    ) -> Dict[str, Any]:
        """
        Run the full voice pipeline and return transcript, AI text, audio, traces, metrics.
        """
        pipeline_start = time.perf_counter()
        session_id = session_id or str(uuid.uuid4())

        # Ensure Groq session memory exists for tool continuity
        ai_service.register_session(session_id)

        traces: List[dict] = []
        metrics = {
            "stt_latency_ms": 0.0,
            "llm_latency_ms": 0.0,
            "tts_latency_ms": 0.0,
            "total_latency_ms": 0.0,
        }

        # ── 1. Speech-to-text ─────────────────────────────────────────────
        stt = get_stt_service()
        if not stt:
            return self._error_response(
                session_id,
                "Speech recognition is not configured. Set DEEPGRAM_API_KEY.",
                metrics,
                traces,
            )

        stt_result = await stt.transcribe_audio(
            audio_bytes,
            content_type=content_type,
            language_hint=language_hint,
        )
        metrics["stt_latency_ms"] = stt_result.get("latency_ms", 0)

        if stt_result.get("error"):
            return self._error_response(
                session_id,
                stt_result["error"],
                metrics,
                traces,
                transcript=stt_result.get("transcript", ""),
            )

        transcript = (stt_result.get("transcript") or "").strip()
        detected_language = stt_result.get("detected_language", "en")

        if not transcript:
            return self._error_response(
                session_id,
                "I could not hear any speech. Please try again.",
                metrics,
                traces,
                transcript="",
                detected_language=detected_language,
            )

        logger.info(
            "[VOICE] STT complete | session=%s | lang=%s | text='%s'",
            session_id,
            detected_language,
            transcript[:80],
        )

        # ── 2. Groq LLM + scheduling tools ────────────────────────────────
        async def _collect_trace(event: dict):
            traces.append(event)

        llm_start = time.perf_counter()
        try:
            ai_text, llm_traces = await ai_service.generate_response(
                session_id=session_id,
                user_text=transcript,
                trace_callback=_collect_trace,
                stt_language=detected_language,
                stt_confidence=stt_result.get("confidence"),
            )
            traces.extend(llm_traces)
        except Exception as exc:
            logger.error("[VOICE] LLM failed: %s", exc, exc_info=True)
            ai_text = (
                "I apologize, but I could not process your request right now. "
                "Please try again."
            )
        metrics["llm_latency_ms"] = (time.perf_counter() - llm_start) * 1000

        if not (ai_text or "").strip():
            ai_text = "I processed your request but could not generate a spoken reply."

        logger.info(
            "[VOICE] LLM complete | session=%s | latency=%.1fms | chars=%d",
            session_id,
            metrics["llm_latency_ms"],
            len(ai_text),
        )

        # ── 3. Text-to-speech (optional — never fails the pipeline) ────────
        audio_base64 = ""
        audio_mime = "audio/mpeg"
        tts_result = await synthesize_speech_safe(ai_text, language=detected_language)
        metrics["tts_latency_ms"] = tts_result.get("latency_ms", 0)
        if not tts_result.get("fallback_mode") and tts_result.get("audio_bytes"):
            audio_base64 = base64.b64encode(tts_result["audio_bytes"]).decode("utf-8")
            audio_mime = tts_result.get("mime_type", "audio/mpeg")

        metrics["total_latency_ms"] = (time.perf_counter() - pipeline_start) * 1000

        logger.info(
            "[VOICE] Pipeline complete | session=%s | STT=%.0fms LLM=%.0fms TTS=%.0fms TOTAL=%.0fms",
            session_id,
            metrics["stt_latency_ms"],
            metrics["llm_latency_ms"],
            metrics["tts_latency_ms"],
            metrics["total_latency_ms"],
        )

        return {
            "session_id": session_id,
            "transcript": transcript,
            "detected_language": detected_language,
            "ai_response": ai_text,
            "audio_base64": audio_base64,
            "audio_mime_type": audio_mime,
            "reasoning_traces": traces,
            "metrics": metrics,
            "success": True,
        }

    def _error_response(
        self,
        session_id: str,
        message: str,
        metrics: dict,
        traces: List[dict],
        transcript: str = "",
        detected_language: str = "en",
    ) -> Dict[str, Any]:
        metrics["total_latency_ms"] = (
            metrics.get("stt_latency_ms", 0)
            + metrics.get("llm_latency_ms", 0)
            + metrics.get("tts_latency_ms", 0)
        )
        return {
            "session_id": session_id,
            "transcript": transcript,
            "detected_language": detected_language,
            "ai_response": message,
            "audio_base64": "",
            "audio_mime_type": "audio/mpeg",
            "reasoning_traces": traces,
            "metrics": metrics,
            "success": False,
        }


# Lazy singleton — does not import Deepgram SDK at module load
voice_service = VoiceService()
