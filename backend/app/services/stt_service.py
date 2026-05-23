"""
stt_service.py
--------------
Speech-to-text via Deepgram prerecorded API (httpx).

Supports browser MediaRecorder formats (webm, wav, mp4) with automatic
language detection for English, Hindi, and Tamil.
"""

import logging
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Deepgram BCP-47 / ISO codes → app language keys
_LANGUAGE_MAP = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "hi": "hi",
    "ta": "ta",
    "tamil": "ta",
    "hindi": "hi",
}


def _normalize_language(detected: Optional[str]) -> str:
    if not detected:
        return "en"
    key = detected.lower().strip()
    if key in _LANGUAGE_MAP:
        return _LANGUAGE_MAP[key]
    prefix = key.split("-")[0]
    return _LANGUAGE_MAP.get(prefix, "en")


class STTService:
    """Transcribe uploaded audio using Deepgram."""

    def __init__(self, api_key: str):
        if not api_key or api_key.startswith("your_"):
            raise ValueError("DEEPGRAM_API_KEY is not configured.")
        self.api_key = api_key
        logger.info("Deepgram STT service initialized (prerecorded API).")

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
        language_hint: str = "auto",
    ) -> dict:
        """
        Transcribe audio bytes.

        Returns:
            {
              "transcript": str,
              "detected_language": "en" | "hi" | "ta",
              "confidence": float | None,
              "latency_ms": float,
            }
        """
        if not audio_bytes:
            return {
                "transcript": "",
                "detected_language": "en",
                "confidence": None,
                "latency_ms": 0,
                "error": "Empty audio payload.",
            }

        url = "https://api.deepgram.com/v1/listen"
        params: dict = {
            "model": "nova-2",
            "smart_format": "true",
            "detect_language": "true",
            "punctuate": "true",
        }

        # Optional hint for Deepgram when not auto
        if language_hint and language_hint != "auto":
            lang = language_hint.lower()
            if lang in ("en", "hi", "ta", "es", "fr", "zh"):
                params["language"] = lang

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": content_type or "audio/webm",
        }

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    url,
                    params=params,
                    headers=headers,
                    content=audio_bytes,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            detail = exc.response.text[:300]
            logger.error("[STT] Deepgram HTTP %s: %s", exc.response.status_code, detail)
            return {
                "transcript": "",
                "detected_language": "en",
                "confidence": None,
                "latency_ms": latency_ms,
                "error": f"Speech recognition failed: {detail}",
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.error("[STT] Deepgram error: %s", exc, exc_info=True)
            return {
                "transcript": "",
                "detected_language": "en",
                "confidence": None,
                "latency_ms": latency_ms,
                "error": str(exc),
            }

        latency_ms = (time.perf_counter() - t0) * 1000

        transcript = ""
        confidence = None
        detected_lang = "en"

        try:
            results = payload.get("results", {})
            channels = results.get("channels", [])
            if channels:
                alt = channels[0].get("alternatives", [])
                if alt:
                    transcript = (alt[0].get("transcript") or "").strip()
                    confidence = alt[0].get("confidence")
            detected_raw = (
                results.get("channels", [{}])[0]
                .get("detected_language")
                or payload.get("metadata", {}).get("detected_language")
            )
            detected_lang = _normalize_language(detected_raw)
        except (IndexError, KeyError, TypeError) as exc:
            logger.warning("[STT] Unexpected Deepgram response shape: %s", exc)

        logger.info(
            "[STT] Transcribed %d chars | lang=%s | latency=%.1fms",
            len(transcript),
            detected_lang,
            latency_ms,
        )

        return {
            "transcript": transcript,
            "detected_language": detected_lang,
            "confidence": confidence,
            "latency_ms": latency_ms,
        }


def get_stt_service() -> Optional[STTService]:
    """Factory — returns None if API key missing."""
    key = settings.DEEPGRAM_API_KEY
    if not key or key.startswith("your_"):
        return None
    try:
        return STTService(api_key=key)
    except ValueError:
        return None
