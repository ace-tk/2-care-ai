"""
tts_service.py
--------------
Text-to-speech via ElevenLabs REST API (httpx).

Failures (401/402/quota/payment) never break the voice pipeline — text-only fallback.
"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
FALLBACK_LOG = "[TTS DISABLED - FALLBACK TEXT MODE]"

# After billing/auth failure, skip ElevenLabs for remainder of process (avoid 402 spam)
_tts_suppressed_reason: Optional[str] = None
_fallback_logged: set[str] = set()


def _log_fallback_once(reason: str) -> None:
    if reason in _fallback_logged:
        return
    _fallback_logged.add(reason)
    logger.warning("%s %s", FALLBACK_LOG, reason)


def is_tts_enabled() -> bool:
    """Whether TTS should be attempted (config + runtime suppression)."""
    if not settings.ENABLE_TTS:
        return False
    if _tts_suppressed_reason:
        return False
    key = settings.ELEVENLABS_API_KEY
    if not key or key.startswith("your_"):
        return False
    return True


def suppress_tts(reason: str) -> None:
    """Disable further ElevenLabs calls this process lifetime."""
    global _tts_suppressed_reason
    _tts_suppressed_reason = reason
    _log_fallback_once(reason)


def _is_billing_or_auth_error(status_code: int, body: str) -> bool:
    if status_code in (401, 402):
        return True
    lower = (body or "").lower()
    return any(
        token in lower
        for token in (
            "payment_required",
            "quota",
            "quota_exceeded",
            "insufficient",
            "subscription",
            "billing",
            "unauthorized",
        )
    )


class TTSService:
    """Convert AI text responses to MP3 audio."""

    def __init__(self, api_key: str, voice_id: Optional[str] = None):
        if not api_key or api_key.startswith("your_"):
            raise ValueError("ELEVENLABS_API_KEY is not configured.")
        self.api_key = api_key
        self.voice_id = voice_id or getattr(
            settings, "ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID
        )
        logger.info("ElevenLabs TTS service initialized.")

    async def synthesize_speech(
        self,
        text: str,
        language: str = "en",
    ) -> dict:
        """
        Synthesize full utterance as MP3.

        Returns:
            { audio_bytes, mime_type, latency_ms, error, fallback_mode }
            Never raises — errors are returned in the dict.
        """
        clean = (text or "").strip()
        if not clean:
            return {
                "audio_bytes": b"",
                "mime_type": "audio/mpeg",
                "latency_ms": 0,
                "error": None,
                "fallback_mode": True,
            }

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": clean,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.4,
                "similarity_boost": 0.75,
            },
        }

        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(url, headers=headers, json=body)
                if response.status_code >= 400:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    snippet = response.text[:300]
                    if _is_billing_or_auth_error(response.status_code, snippet):
                        reason = f"ElevenLabs HTTP {response.status_code} (billing/auth)"
                        suppress_tts(reason)
                        return {
                            "audio_bytes": b"",
                            "mime_type": "audio/mpeg",
                            "latency_ms": latency_ms,
                            "error": None,
                            "fallback_mode": True,
                        }
                    logger.warning(
                        "[TTS] ElevenLabs HTTP %s — text-only fallback",
                        response.status_code,
                    )
                    return {
                        "audio_bytes": b"",
                        "mime_type": "audio/mpeg",
                        "latency_ms": latency_ms,
                        "error": None,
                        "fallback_mode": True,
                    }
                audio_bytes = response.content
        except httpx.HTTPStatusError as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            status = exc.response.status_code
            snippet = exc.response.text[:300]
            if _is_billing_or_auth_error(status, snippet):
                suppress_tts(f"ElevenLabs HTTP {status} (billing/auth)")
            else:
                logger.warning("[TTS] HTTP error %s — text-only fallback", status)
            return {
                "audio_bytes": b"",
                "mime_type": "audio/mpeg",
                "latency_ms": latency_ms,
                "error": None,
                "fallback_mode": True,
            }
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            logger.warning("[TTS] Synthesis failed (%s) — text-only fallback", exc)
            return {
                "audio_bytes": b"",
                "mime_type": "audio/mpeg",
                "latency_ms": latency_ms,
                "error": None,
                "fallback_mode": True,
            }

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "[TTS] Synthesized %d bytes | lang=%s | latency=%.1fms",
            len(audio_bytes),
            language,
            latency_ms,
        )
        return {
            "audio_bytes": audio_bytes,
            "mime_type": "audio/mpeg",
            "latency_ms": latency_ms,
            "error": None,
            "fallback_mode": False,
        }


def get_tts_service() -> Optional[TTSService]:
    if not is_tts_enabled():
        return None
    try:
        return TTSService(api_key=settings.ELEVENLABS_API_KEY)
    except ValueError:
        return None


async def synthesize_speech_safe(
    text: str,
    language: str = "en",
    *,
    timeout_sec: float = 8.0,
) -> dict:
    """
    Safe TTS entry for voice pipeline — never raises, never fails the AI turn.

    Returns:
        audio_bytes, mime_type, latency_ms, fallback_mode (bool)
    """
    if not is_tts_enabled():
        _log_fallback_once(
            "ENABLE_TTS=false"
            if not settings.ENABLE_TTS
            else (_tts_suppressed_reason or "ELEVENLABS not configured")
        )
        return {
            "audio_bytes": b"",
            "mime_type": "audio/mpeg",
            "latency_ms": 0,
            "fallback_mode": True,
        }

    tts = get_tts_service()
    if not tts:
        _log_fallback_once("TTS service unavailable")
        return {
            "audio_bytes": b"",
            "mime_type": "audio/mpeg",
            "latency_ms": 0,
            "fallback_mode": True,
        }

    try:
        result = await asyncio.wait_for(
            tts.synthesize_speech(text, language=language),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError:
        logger.warning("[TTS] Timed out after %.0fs — text-only fallback", timeout_sec)
        return {
            "audio_bytes": b"",
            "mime_type": "audio/mpeg",
            "latency_ms": timeout_sec * 1000,
            "fallback_mode": True,
        }
    except Exception as exc:
        logger.warning("[TTS] Unexpected error (%s) — text-only fallback", exc)
        return {
            "audio_bytes": b"",
            "mime_type": "audio/mpeg",
            "latency_ms": 0,
            "fallback_mode": True,
        }

    if result.get("fallback_mode") or not result.get("audio_bytes"):
        return {
            "audio_bytes": b"",
            "mime_type": result.get("mime_type", "audio/mpeg"),
            "latency_ms": result.get("latency_ms", 0),
            "fallback_mode": True,
        }

    return {
        "audio_bytes": result["audio_bytes"],
        "mime_type": result.get("mime_type", "audio/mpeg"),
        "latency_ms": result.get("latency_ms", 0),
        "fallback_mode": False,
    }
