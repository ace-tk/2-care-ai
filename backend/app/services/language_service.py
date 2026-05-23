"""
language_service.py
-------------------
Fast language detection for English, Hindi, and Tamil.

Priority: script → romanized keywords → STT → Groq (optional) → patient DB → English.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED = frozenset({"en", "hi", "ta"})
CONFIDENCE_THRESHOLD = 0.5
FAST_ROUTE_THRESHOLD = 0.82  # skip LLM intent pre-call when at or above this

_LANG_ALIASES = {
    "en": "en",
    "english": "en",
    "eng": "en",
    "hi": "hi",
    "hindi": "hi",
    "hin": "hi",
    "ta": "ta",
    "tamil": "ta",
    "tam": "ta",
    "other": "en",
    "auto": "en",
}

# Romanized Hindi (Hinglish) — word-boundary patterns
_HINDI_KEYWORDS = re.compile(
    r"\b("
    r"mujhe|meri|mera|mere|hamen|humein|chahiye|chahie|chahiye|karni|karna|karo|karana|"
    r"kal|parso|aaj|subah|shaam|samay|badlo|badal|nikal|nikalo|"
    r"nahi|nahin|haan|hai|hain|ho|hoga|"
    r"appointment|booking|book|dentist|dant|danton|doctor|davakhana|"
    r"cancel|radh|रद्द"
    r")\b",
    re.IGNORECASE,
)

# Tamil script block (fast pre-route)
_TAMIL_SCRIPT = re.compile(r"[\u0B80-\u0BFF]")

# Tamil romanized + mixed
_TAMIL_KEYWORDS = re.compile(
    r"("
    r"[\u0B80-\u0BFF]+|"
    r"\b(venum|venna|vendum|appointment|booking|cancel|pannunga|pannu|doctor)\b"
    r")",
    re.IGNORECASE,
)

# Intent keywords (no LLM required)
_INTENT_CANCEL = re.compile(
    r"\b(cancel|cancelled|cancellation|radd|radh|रद्द|रद्दी|nikal)\b|"
    r"cancel\s+\w*\s*booking|booking\s+cancel",
    re.IGNORECASE,
)
_INTENT_RESCHEDULE = re.compile(
    r"\b(reschedule|rescheduled|badlo|badal|change|shift|move|postpone)\b",
    re.IGNORECASE,
)
_INTENT_BOOK = re.compile(
    r"\b(book|booking|booked|appointment|chahiye|chahie|karni|karna|venum|venna|vendum)\b|"
    r"\b(dentist|dental|doctor)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LanguageDetection:
    code: str
    confidence: float
    source: str
    script_counts: Optional[dict] = None
    route_ms: float = 0.0

    @property
    def display_name(self) -> str:
        return {"en": "English", "hi": "Hindi", "ta": "Tamil"}.get(self.code, "English")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "display_name": self.display_name,
            "route_ms": round(self.route_ms, 2),
        }


def normalize_language_code(raw: Optional[str]) -> str:
    if not raw:
        return "en"
    key = str(raw).lower().strip().replace("_", "-")
    if key in _LANG_ALIASES:
        return _LANG_ALIASES[key]
    prefix = key.split("-")[0]
    return _LANG_ALIASES.get(prefix, "en")


def _count_script_chars(text: str) -> dict[str, int]:
    devanagari = 0
    tamil = 0
    latin = 0
    for ch in text:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F or 0xA8E0 <= cp <= 0xA8FF:
            devanagari += 1
        elif 0x0B80 <= cp <= 0x0BFF:
            tamil += 1
        elif ch.isalpha() and cp < 0x0250:
            latin += 1
    return {"devanagari": devanagari, "tamil": tamil, "latin": latin}


def fast_route_language(text: str) -> LanguageDetection:
    """
    Sub-millisecond pre-routing before any LLM call.
    Romanized Hindi + script detection.
    """
    t0 = time.perf_counter()
    cleaned = (text or "").strip()
    if not cleaned:
        d = LanguageDetection("en", 0.3, "empty_text", route_ms=0)
        logger.info("[LANG DETECT] %s", d.to_dict())
        return d

    counts = _count_script_chars(cleaned)

    # Tamil script — highest priority
    if counts["tamil"] > 0 or _TAMIL_SCRIPT.search(cleaned):
        conf = min(0.98, 0.9 + counts["tamil"] * 0.01)
        d = LanguageDetection("ta", conf, "fast_route_tamil_script", counts, _elapsed_ms(t0))
        logger.info("[LANG DETECT] %s", d.to_dict())
        return d

    # Devanagari → Hindi
    if counts["devanagari"] > 0:
        conf = min(0.98, 0.9 + counts["devanagari"] * 0.01)
        d = LanguageDetection("hi", conf, "fast_route_hindi_script", counts, _elapsed_ms(t0))
        logger.info("[LANG DETECT] %s", d.to_dict())
        return d

    lower = cleaned.lower()
    hi_hits = _HINDI_KEYWORDS.findall(lower)
    ta_hits = _TAMIL_KEYWORDS.findall(lower)

    # Romanized Hindi — e.g. "Mujhe kal dentist appointment book karni hai"
    if len(hi_hits) >= 2:
        conf = min(0.97, 0.78 + len(hi_hits) * 0.04)
        d = LanguageDetection("hi", conf, "fast_route_hindi_keywords", counts, _elapsed_ms(t0))
        logger.info("[LANG DETECT] %s | hits=%s", d.to_dict(), hi_hits[:6])
        return d

    if len(hi_hits) == 1 and any(
        w in lower for w in ("mujhe", "chahiye", "chahie", "karni", "karna", "meri")
    ):
        d = LanguageDetection("hi", 0.88, "fast_route_hindi_keyword_single", counts, _elapsed_ms(t0))
        logger.info("[LANG DETECT] %s", d.to_dict())
        return d

    if ta_hits and counts["latin"] > 0:
        d = LanguageDetection("ta", 0.85, "fast_route_tamil_mixed", counts, _elapsed_ms(t0))
        logger.info("[LANG DETECT] %s", d.to_dict())
        return d

    if counts["latin"] > 0:
        d = LanguageDetection("en", 0.7, "latin_default", counts, _elapsed_ms(t0))
        logger.info("[LANG DETECT] %s", d.to_dict())
        return d

    d = LanguageDetection("en", 0.45, "fallback", counts, _elapsed_ms(t0))
    logger.info("[LANG DETECT] %s", d.to_dict())
    return d


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def detect_from_text(text: str) -> LanguageDetection:
    """Full text detection (delegates to fast_route)."""
    return fast_route_language(text)


def detect_intent_from_text(text: str) -> str:
    """Fast local intent — avoids extra Groq call for multilingual."""
    if not text or not text.strip():
        return "general"
    if _INTENT_CANCEL.search(text):
        return "cancellation"
    if _INTENT_RESCHEDULE.search(text):
        return "rescheduling"
    if _INTENT_BOOK.search(text):
        return "booking"
    return "general"


def should_skip_llm_intent(detection: LanguageDetection) -> bool:
    """Skip the slow Groq intent pre-call when pre-route is confident."""
    return detection.confidence >= FAST_ROUTE_THRESHOLD and detection.code in SUPPORTED


def detect_from_stt(
    stt_language: Optional[str],
    stt_confidence: Optional[float] = None,
) -> LanguageDetection:
    code = normalize_language_code(stt_language)
    conf = stt_confidence if stt_confidence is not None else 0.75
    return LanguageDetection(code, min(1.0, max(0.0, conf)), "deepgram_stt")


def merge_detections(
    *,
    text: Optional[str] = None,
    pre_detected: Optional[LanguageDetection] = None,
    stt_language: Optional[str] = None,
    stt_confidence: Optional[float] = None,
    groq_language: Optional[str] = None,
    groq_confidence: Optional[float] = None,
    patient_preference: Optional[str] = None,
    session_language: Optional[str] = None,
) -> LanguageDetection:
    candidates: list[LanguageDetection] = []

    if pre_detected:
        candidates.append(pre_detected)
    if text and text.strip() and not pre_detected:
        candidates.append(fast_route_language(text))
    if stt_language:
        candidates.append(detect_from_stt(stt_language, stt_confidence))
    if groq_language:
        code = normalize_language_code(groq_language)
        conf = groq_confidence if groq_confidence is not None else 0.7
        candidates.append(LanguageDetection(code, conf, "groq_intent"))

    if not candidates:
        if patient_preference:
            return LanguageDetection(
                normalize_language_code(patient_preference), 0.85, "patient_db"
            )
        if session_language:
            return LanguageDetection(
                normalize_language_code(session_language), 0.8, "session_carryover"
            )
        return LanguageDetection("en", 1.0, "default")

    # Prefer fast_route / script sources over groq when scores are close
    def _rank(c: LanguageDetection) -> tuple:
        source_boost = 0.15 if c.source.startswith("fast_route") else 0.0
        return (c.confidence + source_boost, c.confidence)

    best = max(candidates, key=_rank)

    if best.confidence < CONFIDENCE_THRESHOLD:
        if patient_preference:
            logger.info("[LANG ROUTE] low conf → patient pref %s", patient_preference)
            return LanguageDetection(
                normalize_language_code(patient_preference), 0.85, "patient_db_fallback"
            )
        logger.info("[LANG ROUTE] low conf → English default")
        return LanguageDetection("en", 0.5, "low_confidence_default")

    logger.info("[LANG ROUTE] selected=%s conf=%.2f source=%s", best.code, best.confidence, best.source)
    return best


def language_instruction(code: str, *, compact: bool = False) -> str:
    if code == "hi":
        if compact:
            return "CRITICAL: Respond ONLY in Hindi (हिंदी). Never use English."
        return (
            "CRITICAL: The user writes in Hindi. Respond ONLY in Hindi (हिंदी). "
            "Never use English unless quoting a booking ID."
        )
    if code == "ta":
        if compact:
            return "CRITICAL: Respond ONLY in Tamil (தமிழ்). Never use English."
        return (
            "CRITICAL: The user writes in Tamil. Respond ONLY in Tamil (தமிழ்). "
            "Never use English unless quoting a booking ID."
        )
    return "CRITICAL: Respond ONLY in English."


def extract_time_change_hint(text: str) -> Optional[str]:
    if not text:
        return None
    lower = text.lower()
    if not any(k in lower for k in ("pm", "am", ":", "o'clock", "make it", "instead", "change", "badlo")):
        return None
    match = re.search(
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
        lower,
        re.IGNORECASE,
    )
    return match.group(1) if match else None
