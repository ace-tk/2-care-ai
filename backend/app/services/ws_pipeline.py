"""
WebSocket voice/chat pipeline — shared handler for text and audio messages.

Flow:
  user input (text or audio) → STT (if audio) → Groq + tools → emit text → TTS (optional)
"""

import asyncio
import base64
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from app.services.ai_service import ai_service
from app.services import session_memory
from app.services.language_service import fast_route_language, normalize_language_code
from app.services.multilingual_fallbacks import get_error_message, get_timeout_message
from app.services.ws_session import get_generation, is_stale
from app.services.stt_service import get_stt_service
from app.services.tts_service import synthesize_speech_safe

logger = logging.getLogger(__name__)

# Timeouts (seconds) — booking tool loops need >10s; pre-route is <5ms
LLM_TIMEOUT_SEC = 28.0
TTS_TIMEOUT_SEC = 8.0
PIPELINE_TIMEOUT_SEC = 50.0


async def emit_metrics(
    websocket: WebSocket,
    session_id: str,
    metrics: dict,
    *,
    generation: Optional[int] = None,
) -> None:
    """Push latency breakdown to client (for dashboard)."""
    if generation is not None and is_stale(session_id, generation):
        return
    await websocket.send_json({
        "type": "metrics",
        "session_id": session_id,
        "metrics": metrics,
    })


async def emit_assistant_response(
    websocket: WebSocket,
    session_id: str,
    *,
    text: str,
    transcript: str = "",
    detected_language: str = "en",
    audio_base64: Optional[str] = None,
    audio_mime_type: str = "audio/mpeg",
    metrics: Optional[dict] = None,
    traces: Optional[List[dict]] = None,
    error: Optional[str] = None,
    generation: Optional[int] = None,
) -> None:
    if generation is not None and is_stale(session_id, generation):
        logger.info("[WS] Dropped stale assistant_response | session=%s | gen=%s", session_id, generation)
        return
    """
    Send assistant reply to the client (canonical + legacy event shapes).
    Text is always sent even when audio_base64 is empty.
    """
    metrics = metrics or {}
    payload = {
        "text": text,
        "transcript": transcript,
        "detected_language": detected_language,
        "audio_base64": audio_base64,
        "audio_mime_type": audio_mime_type,
        "metrics": metrics,
        "error": error,
    }

    canonical = {
        "type": "assistant_response",
        "session_id": session_id,
        "text": text,
        "audio_url": None,
        "audio_base64": audio_base64,
        "payload": payload,
    }
    logger.info(
        "[WS OUT] assistant_response | session=%s | text_len=%d | has_audio=%s | STT=%.0fms LLM=%.0fms TTS=%.0fms TOTAL=%.0fms",
        session_id,
        len(text or ""),
        bool(audio_base64),
        metrics.get("stt_latency_ms", 0),
        metrics.get("llm_latency_ms", 0),
        metrics.get("tts_latency_ms", 0),
        metrics.get("total_latency_ms", 0),
    )

    await websocket.send_json(canonical)
    if metrics:
        await emit_metrics(websocket, session_id, metrics, generation=generation)

    if traces:
        for trace in traces:
            await websocket.send_json({
                "event": "reasoning_trace",
                "session_id": session_id,
                "payload": trace,
            })


async def emit_transcript_event(
    websocket: WebSocket,
    session_id: str,
    transcript: str,
    detected_language: str,
) -> None:
    """Notify frontend that STT completed (before LLM)."""
    logger.info(
        "[WS OUT] transcript | session=%s | lang=%s | text='%s'",
        session_id,
        detected_language,
        transcript[:80],
    )
    await websocket.send_json({
        "type": "transcript",
        "event": "transcript",
        "session_id": session_id,
        "text": transcript,
        "detected_language": detected_language,
        "payload": {
            "original_text": transcript,
            "translated_text": transcript,
            "language": detected_language,
            "is_final": True,
        },
    })


async def _run_llm(
    session_id: str,
    user_text: str,
    traces_out: List[dict],
    *,
    stt_language: Optional[str] = None,
    stt_confidence: Optional[float] = None,
) -> tuple[str, List[dict]]:
    ai_text, traces = await asyncio.wait_for(
        ai_service.generate_response(
            session_id=session_id,
            user_text=user_text,
            stt_language=stt_language,
            stt_confidence=stt_confidence,
        ),
        timeout=LLM_TIMEOUT_SEC,
    )
    traces_out.extend(traces)
    return ai_text, traces


async def _run_tts(text: str, language: str) -> tuple[str, float]:
    """Returns (audio_base64, latency_ms). Empty string = text-only mode (not an error)."""
    if not (text or "").strip():
        return "", 0.0

    result = await synthesize_speech_safe(
        text, language=language, timeout_sec=TTS_TIMEOUT_SEC
    )
    latency_ms = result.get("latency_ms", 0)

    if result.get("fallback_mode") or not result.get("audio_bytes"):
        return "", latency_ms

    b64 = base64.b64encode(result["audio_bytes"]).decode("utf-8")
    return b64, latency_ms


async def process_text_turn(
    websocket: WebSocket,
    session_id: str,
    user_text: str,
    *,
    language_hint: str = "en",
    generation: Optional[int] = None,
    on_trace=None,
) -> None:
    """Process a text utterance and respond over the websocket."""
    pipeline_start = time.perf_counter()
    user_text = (user_text or "").strip()
    if not user_text:
        return

    ai_service.register_session(session_id)
    metrics: Dict[str, Any] = {
        "stt_latency_ms": 0.0,
        "llm_latency_ms": 0.0,
        "tts_latency_ms": 0.0,
        "total_latency_ms": 0.0,
    }
    traces: List[dict] = []

    text_detection = fast_route_language(user_text)
    logger.info(
        "[WS] Incoming transcript (text) | session=%s | lang=%s | '%s'",
        session_id,
        text_detection.code,
        user_text[:80],
    )
    await emit_transcript_event(websocket, session_id, user_text, text_detection.code)
    if generation is not None and is_stale(session_id, generation):
        return

    llm_start = time.perf_counter()
    try:
        ai_text, llm_traces = await _run_llm(
            session_id,
            user_text,
            traces,
            stt_language=text_detection.code,
            stt_confidence=text_detection.confidence,
        )
        traces.extend(llm_traces)
    except asyncio.TimeoutError:
        lang = session_memory.get_session(session_id).language_code
        logger.error("[WS] LLM timed out after %.0fs | session=%s | lang=%s", LLM_TIMEOUT_SEC, session_id, lang)
        ai_text = get_timeout_message(lang)
    except Exception as exc:
        lang = session_memory.get_session(session_id).language_code
        logger.error("[WS] LLM failed | session=%s | %s", session_id, exc, exc_info=True)
        ai_text = get_error_message(lang)

    metrics["llm_latency_ms"] = (time.perf_counter() - llm_start) * 1000
    ai_text = (ai_text or "").strip() or "I processed your request but could not generate a reply."

    logger.info(
        "[WS] Groq response | session=%s | preview='%s' | llm_ms=%.0f",
        session_id,
        ai_text[:100],
        metrics["llm_latency_ms"],
    )

    # Send text immediately (before TTS)
    metrics["total_latency_ms"] = (time.perf_counter() - pipeline_start) * 1000
    if generation is not None and is_stale(session_id, generation):
        return

    reply_lang = session_memory.get_session(session_id).language_code
    await emit_assistant_response(
        websocket,
        session_id,
        text=ai_text,
        transcript=user_text,
        detected_language=reply_lang,
        audio_base64=None,
        metrics=dict(metrics),
        traces=[],
        generation=generation,
    )

    if generation is not None and is_stale(session_id, generation):
        return

    # TTS in background path (non-blocking for chat UI)
    audio_b64, tts_ms = await _run_tts(ai_text, reply_lang)
    metrics["tts_latency_ms"] = tts_ms
    metrics["total_latency_ms"] = (time.perf_counter() - pipeline_start) * 1000

    if audio_b64 and not (generation is not None and is_stale(session_id, generation)):
        await websocket.send_json({
            "type": "assistant_audio",
            "event": "audio_stream",
            "session_id": session_id,
            "audio_base64": audio_b64,
            "payload": {"audio_data": audio_b64, "audio_base64": audio_b64},
        })
        logger.info("[WS OUT] assistant_audio | session=%s | tts_ms=%.0f", session_id, tts_ms)

    if metrics:
        await emit_metrics(websocket, session_id, dict(metrics), generation=generation)

    # Send traces after main response
    for trace in traces:
        await websocket.send_json({
            "event": "reasoning_trace",
            "session_id": session_id,
            "payload": trace,
        })

    logger.info(
        "[WS] Pipeline complete | session=%s | STT=%.0fms LLM=%.0fms TTS=%.0fms TOTAL=%.0fms",
        session_id,
        metrics["stt_latency_ms"],
        metrics["llm_latency_ms"],
        metrics["tts_latency_ms"],
        metrics["total_latency_ms"],
    )


async def process_audio_turn(
    websocket: WebSocket,
    session_id: str,
    audio_bytes: bytes,
    *,
    content_type: str = "audio/webm",
    language_hint: str = "auto",
    generation: Optional[int] = None,
) -> None:
    """STT → Groq → respond over websocket."""
    pipeline_start = time.perf_counter()
    ai_service.register_session(session_id)

    metrics: Dict[str, Any] = {
        "stt_latency_ms": 0.0,
        "llm_latency_ms": 0.0,
        "tts_latency_ms": 0.0,
        "total_latency_ms": 0.0,
    }
    traces: List[dict] = []

    stt = get_stt_service()
    if not stt:
        await emit_assistant_response(
            websocket,
            session_id,
            text="Speech recognition is not configured. Please set DEEPGRAM_API_KEY.",
            metrics=metrics,
            error="stt_not_configured",
        )
        return

    logger.info("[WS] Incoming audio | session=%s | bytes=%d", session_id, len(audio_bytes))
    stt_result = await stt.transcribe_audio(
        audio_bytes, content_type=content_type, language_hint=language_hint
    )
    metrics["stt_latency_ms"] = stt_result.get("latency_ms", 0)

    if stt_result.get("error"):
        await emit_assistant_response(
            websocket,
            session_id,
            text=stt_result["error"],
            metrics=metrics,
            error="stt_failed",
        )
        return

    transcript = (stt_result.get("transcript") or "").strip()
    detected_language = normalize_language_code(stt_result.get("detected_language", "en"))
    stt_confidence = stt_result.get("confidence")

    logger.info(
        "[WS] STT transcript | session=%s | lang=%s | conf=%s | '%s'",
        session_id,
        detected_language,
        stt_confidence,
        transcript[:80],
    )

    if not transcript:
        await emit_assistant_response(
            websocket,
            session_id,
            text="I could not hear any speech. Please try again.",
            transcript="",
            detected_language=detected_language,
            metrics=metrics,
            error="empty_transcript",
        )
        return

    await emit_transcript_event(websocket, session_id, transcript, detected_language)
    await emit_metrics(
        websocket,
        session_id,
        {"stt_latency_ms": metrics["stt_latency_ms"], "partial": True},
        generation=generation,
    )
    if generation is not None and is_stale(session_id, generation):
        return

    llm_start = time.perf_counter()
    try:
        ai_text, llm_traces = await _run_llm(
            session_id,
            transcript,
            traces,
            stt_language=detected_language,
            stt_confidence=stt_confidence,
        )
        traces.extend(llm_traces)
    except asyncio.TimeoutError:
        lang = session_memory.get_session(session_id).language_code or detected_language
        logger.error("[WS] LLM timed out | session=%s | lang=%s", session_id, lang)
        ai_text = get_timeout_message(lang)
    except Exception as exc:
        lang = session_memory.get_session(session_id).language_code or detected_language
        logger.error("[WS] LLM failed | session=%s | %s", session_id, exc, exc_info=True)
        ai_text = get_error_message(lang)

    metrics["llm_latency_ms"] = (time.perf_counter() - llm_start) * 1000
    ai_text = (ai_text or "").strip() or "I processed your request."

    logger.info("[WS] Groq response | session=%s | '%s'", session_id, ai_text[:100])

    if generation is not None and is_stale(session_id, generation):
        return

    reply_lang = session_memory.get_session(session_id).language_code
    metrics["total_latency_ms"] = (time.perf_counter() - pipeline_start) * 1000
    await emit_assistant_response(
        websocket,
        session_id,
        text=ai_text,
        transcript=transcript,
        detected_language=reply_lang,
        metrics=dict(metrics),
        generation=generation,
    )

    if generation is not None and is_stale(session_id, generation):
        return

    audio_b64, tts_ms = await _run_tts(ai_text, reply_lang)
    metrics["tts_latency_ms"] = tts_ms
    metrics["total_latency_ms"] = (time.perf_counter() - pipeline_start) * 1000

    if audio_b64:
        await websocket.send_json({
            "type": "assistant_audio",
            "event": "audio_stream",
            "session_id": session_id,
            "audio_base64": audio_b64,
            "payload": {"audio_data": audio_b64},
        })

    for trace in traces:
        await websocket.send_json({
            "event": "reasoning_trace",
            "session_id": session_id,
            "payload": trace,
        })

    logger.info(
        "[WS] Audio pipeline complete | session=%s | STT=%.0fms LLM=%.0fms TTS=%.0fms TOTAL=%.0fms",
        session_id,
        metrics["stt_latency_ms"],
        metrics["llm_latency_ms"],
        metrics["tts_latency_ms"],
        metrics["total_latency_ms"],
    )


async def process_turn_with_timeout(
    websocket: WebSocket,
    session_id: str,
    *,
    user_text: Optional[str] = None,
    audio_bytes: Optional[bytes] = None,
    content_type: str = "audio/webm",
    language_hint: str = "auto",
    generation: Optional[int] = None,
) -> None:
    """Wrap pipeline in overall timeout; emit graceful error on expiry."""
    if generation is None:
        generation = get_generation(session_id)
    try:
        if audio_bytes is not None:
            coro = process_audio_turn(
                websocket,
                session_id,
                audio_bytes,
                content_type=content_type,
                language_hint=language_hint,
                generation=generation,
            )
        elif user_text:
            coro = process_text_turn(
                websocket,
                session_id,
                user_text,
                language_hint=language_hint,
                generation=generation,
            )
        else:
            return

        await asyncio.wait_for(coro, timeout=PIPELINE_TIMEOUT_SEC)
    except asyncio.CancelledError:
        logger.info("[WS] Turn cancelled | session=%s | gen=%s", session_id, generation)
        raise
    except asyncio.TimeoutError:
        if is_stale(session_id, generation):
            return
        lang = session_memory.get_session(session_id).language_code
        logger.error("[WS] Pipeline timeout %.0fs | session=%s | lang=%s", PIPELINE_TIMEOUT_SEC, session_id, lang)
        await emit_assistant_response(
            websocket,
            session_id,
            text=get_timeout_message(lang),
            error="pipeline_timeout",
            metrics={"total_latency_ms": PIPELINE_TIMEOUT_SEC * 1000},
            generation=generation,
        )
