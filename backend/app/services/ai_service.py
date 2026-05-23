"""
ai_service.py
--------------
Provider-agnostic AI orchestration service for realtime WebSocket chat.

Provider priority:
  1. Groq (llama-3.3-70b-versatile) with scheduling tools  (primary)
  2. Static error response                                   (fallback)

The WebSocket layer (voice.py) calls:
  ai_text, traces = await ai_service.generate_response(session_id, user_text)

Traces are a list of dicts emitted during orchestration that the frontend
renders in the "AI Clinical Reasoning Trace" panel.
"""

import logging
import time
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class AIService:
    """Singleton AI orchestration layer used by voice.py."""

    def __init__(self):
        self._provider = None
        logger.info("AIService created.")

    def _get_provider(self):
        if self._provider is None:
            try:
                from app.core.config import settings
                key = settings.GROQ_API_KEY
                if not key or key.startswith("your_"):
                    logger.warning("[AIService] GROQ_API_KEY not set — AI responses unavailable.")
                    return None
                from app.services.groq_service import GroqService
                self._provider = GroqService(api_key=key)
                logger.info("[AIService] GroqService initialized (llama-3.3-70b-versatile).")
            except Exception as e:
                logger.error(f"[AIService] Failed to init GroqService: {e}", exc_info=True)
                return None
        return self._provider

    def register_session(self, session_id: str) -> None:
        provider = self._get_provider()
        if provider:
            provider.register_session(session_id)
        logger.info(f"[AIService] Session registered: {session_id}")

    def unregister_session(self, session_id: str) -> None:
        provider = self._get_provider()
        if provider:
            provider.unregister_session(session_id)
        logger.info(f"[AIService] Session unregistered: {session_id}")

    async def generate_response(
        self,
        session_id: str,
        user_text: str,
        trace_callback=None,
        *,
        stt_language: str | None = None,
        stt_confidence: float | None = None,
    ) -> Tuple[str, List[dict]]:
        """
        Generate AI response + reasoning traces.

        Returns:
            (ai_response_text, [trace_event, ...])
        """
        traces: List[dict] = []

        async def _collect_trace(event: dict):
            traces.append(event)
            if trace_callback:
                await trace_callback(event)

        t_start = time.perf_counter()
        provider = self._get_provider()

        if provider:
            try:
                logger.info(
                    f"[AIService] → Groq | session={session_id} | text='{user_text[:80]}'"
                )
                reply = await provider.generate_response(
                    session_id=session_id,
                    user_text=user_text,
                    trace_callback=_collect_trace,
                    stt_language=stt_language,
                    stt_confidence=stt_confidence,
                )
                total_ms = (time.perf_counter() - t_start) * 1000
                logger.info(
                    f"[AIService] ← Groq OK | session={session_id} | "
                    f"latency={total_ms:.1f}ms | chars={len(reply)} | traces={len(traces)}"
                )
                return reply, traces

            except Exception as e:
                total_ms = (time.perf_counter() - t_start) * 1000
                logger.error(
                    f"[AIService] Groq failed after {total_ms:.1f}ms | "
                    f"session={session_id} | error={e}",
                    exc_info=True,
                )

        # ── Fallback ─────────────────────────────────────────────────────────
        reply = "I apologize, but I am currently unable to process your request due to an AI processing error. Please check the system logs or your API key."
        total_ms = (time.perf_counter() - t_start) * 1000
        traces.append({"node": "error", "timestamp": time.time(), "latency_ms": total_ms})
        logger.info(f"[AIService] AI failed | session={session_id} | latency={total_ms:.1f}ms")
        return reply, traces


ai_service = AIService()
