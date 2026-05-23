"""
memory_manager.py
-----------------
Central memory orchestration: language resolution, session + patient context,
prompt injection, and post-turn persistence.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

from app.core.database import AsyncSessionLocal
from app.services import session_memory
from app.services.language_service import (
    LanguageDetection,
    extract_time_change_hint,
    fast_route_language,
    language_instruction,
    merge_detections,
    normalize_language_code,
)
from app.services.patient_memory_service import (
    PatientMemorySnapshot,
    load_patient_memory,
    update_language_preference,
    update_last_interaction_summary,
    set_preferred_doctor,
)

logger = logging.getLogger(__name__)

TraceCallback = Optional[Callable[[dict], Awaitable[None]]]

# Cached patient snapshots per session (avoid repeated DB hits each turn)
_patient_cache: dict[str, PatientMemorySnapshot] = {}


async def bind_patient_to_session(session_id: str, patient_id: int) -> Optional[PatientMemorySnapshot]:
    """Load persistent patient memory when a voice/chat session starts."""
    session_memory.update_session(session_id, patient_id=patient_id)

    async with AsyncSessionLocal() as db:
        snapshot = await load_patient_memory(db, patient_id)

    if snapshot:
        _patient_cache[session_id] = snapshot
        session_memory.update_session(
            session_id,
            language_code=snapshot.language_preference,
            language_confidence=0.9,
            language_source="patient_db",
        )
        logger.info(
            "[Memory] Patient bound | session=%s | patient=%s | lang=%s",
            session_id,
            patient_id,
            snapshot.language_preference,
        )
    return snapshot


def get_patient_snapshot(session_id: str) -> Optional[PatientMemorySnapshot]:
    return _patient_cache.get(session_id)


def clear_session_cache(session_id: str) -> None:
    _patient_cache.pop(session_id, None)


async def resolve_language(
    session_id: str,
    user_text: str,
    *,
    pre_detected: Optional[LanguageDetection] = None,
    stt_language: Optional[str] = None,
    stt_confidence: Optional[float] = None,
    groq_language: Optional[str] = None,
    groq_confidence: Optional[float] = None,
) -> LanguageDetection:
    sess = session_memory.get_session(session_id)
    patient_pref = None
    snapshot = get_patient_snapshot(session_id)
    if snapshot:
        patient_pref = snapshot.language_preference

    if pre_detected is None and user_text:
        pre_detected = fast_route_language(user_text)

    detection = merge_detections(
        pre_detected=pre_detected,
        stt_language=stt_language,
        stt_confidence=stt_confidence,
        groq_language=groq_language,
        groq_confidence=groq_confidence,
        patient_preference=patient_pref,
        session_language=sess.language_code,
    )

    session_memory.update_session(
        session_id,
        language_code=detection.code,
        language_confidence=detection.confidence,
        language_source=detection.source,
    )
    return detection


def apply_time_change_hint(session_id: str, user_text: str) -> Optional[str]:
    """If user adjusts time, update pending slot context."""
    hint = extract_time_change_hint(user_text)
    if not hint:
        return None

    sess = session_memory.get_session(session_id)
    if not sess.selected_slot and not sess.appointment_time:
        return None

    base = sess.selected_slot or sess.appointment_time or ""
    date_part = base.split(" ")[0] if " " in base else sess.appointment_date

    pending = f"User wants to change time to {hint}"
    if date_part:
        pending += f" (keep date {date_part}, same doctor/specialty)"

    session_memory.update_session(
        session_id,
        pending_confirmation=pending,
        workflow_state="awaiting_confirmation",
    )
    return pending


def build_prompt_context(
    session_id: str,
    patient_snapshot: Optional[PatientMemorySnapshot] = None,
    *,
    compact: bool = False,
    max_chars: int = 900,
) -> tuple[str, dict]:
    """
    Build memory block for Groq system prompt.
    compact=True trims tokens for hi/ta to reduce LLM latency.
    """
    sess = session_memory.get_session(session_id)
    sections: list[str] = []
    meta: dict = {"session_state": sess.to_dict(), "compact": compact}

    if patient_snapshot:
        block = patient_snapshot.to_prompt_block(compact=compact)
        if compact:
            sections.append(f"Patient: {patient_snapshot.full_name} | lang={patient_snapshot.language_preference}")
            if patient_snapshot.last_interaction_summary:
                sections.append(f"Last: {patient_snapshot.last_interaction_summary[:120]}")
            if patient_snapshot.recent_appointments:
                last = patient_snapshot.recent_appointments[0]
                sections.append(
                    f"Recent appt: {last.get('booking_id')} {last.get('time')}"
                )
        else:
            sections.append(block)
        meta["patient_memory"] = patient_snapshot.to_dict()

    session_lines = [
        f"state={sess.workflow_state} intent={sess.last_intent} lang={sess.language_code}",
    ]
    if sess.selected_doctor_name:
        session_lines.append(f"doctor={sess.selected_doctor_name}")
    if sess.requested_specialty:
        session_lines.append(f"specialty={sess.requested_specialty}")
    if sess.selected_slot:
        session_lines.append(f"slot={sess.selected_slot}")
    elif sess.offered_slots:
        session_lines.append(f"slots={', '.join(sess.offered_slots[:3])}")
    if sess.active_appointment_id:
        session_lines.append(f"appt=APT-{sess.active_appointment_id}")
    if sess.pending_confirmation and not compact:
        session_lines.append(f"pending={sess.pending_confirmation[:80]}")

    sections.append("Session: " + " | ".join(session_lines))
    if not compact:
        sections.append("Keep same doctor/date when user changes time only.")

    text = "\n".join(sections)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    meta["injected_chars"] = len(text)
    return text, meta


async def emit_memory_traces(
    trace_callback: TraceCallback,
    *,
    language: LanguageDetection,
    patient_snapshot: Optional[PatientMemorySnapshot],
    context_meta: dict,
    injected_preview: str,
) -> None:
    if not trace_callback:
        return

    await trace_callback(
        {
            "node": "language_detection",
            "timestamp": time.time(),
            "intent": f"{language.display_name} ({language.code})",
            "language": language.to_dict(),
            "confidence": language.confidence,
            "source": language.source,
        }
    )

    if patient_snapshot:
        await trace_callback(
            {
                "node": "memory_retrieval",
                "timestamp": time.time(),
                "tool_result": patient_snapshot.to_prompt_block()[:500],
                "memory": patient_snapshot.to_dict(),
            }
        )

    await trace_callback(
        {
            "node": "context_injection",
            "timestamp": time.time(),
            "tool_result": injected_preview[:600],
            "injected_context": context_meta,
        }
    )

    sess = session_memory.get_session(context_meta["session_state"]["session_id"])
    await trace_callback(
        {
            "node": "session_state",
            "timestamp": time.time(),
            "tool_result": sess.summary_for_trace(),
            "session_state": context_meta["session_state"],
        }
    )


async def persist_turn(
    session_id: str,
    *,
    user_text: str,
    assistant_text: str,
    intent: str,
    language_code: str,
) -> None:
    """Update session + database after a successful turn."""
    sess = session_memory.get_session(session_id)
    session_memory.update_session(
        session_id,
        last_user_message=user_text,
        previous_assistant_response=assistant_text,
        last_intent=intent,
    )

    patient_id = sess.patient_id
    if not patient_id:
        return

    async with AsyncSessionLocal() as db:
        await update_language_preference(db, patient_id, language_code)
        await update_last_interaction_summary(
            db, patient_id, user_text, assistant_text, intent
        )


def update_from_tool_result(session_id: str, tool_name: str, result: dict) -> None:
    """Refresh session memory from scheduling tool outcomes."""
    sess = session_memory.get_session(session_id)

    if tool_name == "search_doctors_by_specialty" and result.get("success"):
        doctors = result.get("doctors") or []
        if doctors:
            first = doctors[0]
            session_memory.update_session(
                session_id,
                selected_doctor_id=first.get("id"),
                selected_doctor_name=first.get("name"),
                requested_specialty=result.get("specialty") or first.get("specialty"),
                workflow_state="doctor_selected",
            )

    elif tool_name == "get_available_slots" and result.get("success"):
        slots = result.get("slots") or []
        session_memory.update_session(
            session_id,
            offered_slots=slots,
            selected_doctor_id=result.get("doctor_id") or sess.selected_doctor_id,
            selected_doctor_name=result.get("doctor") or sess.selected_doctor_name,
            workflow_state="slots_offered",
            pending_confirmation="User may pick or adjust a slot time",
        )
        if slots:
            session_memory.update_session(session_id, selected_slot=slots[0])

    elif tool_name == "find_nearest_available_slot" and result.get("success"):
        slot = result.get("slot_time")
        session_memory.update_session(
            session_id,
            selected_slot=slot,
            appointment_time=slot,
            workflow_state="slot_offered",
        )

    elif tool_name == "book_appointment" and result.get("success"):
        appt = result.get("appointment") or {}
        session_memory.update_session(
            session_id,
            active_appointment_id=appt.get("id"),
            selected_doctor_name=result.get("doctor"),
            appointment_time=result.get("time"),
            selected_slot=result.get("time"),
            booking_status="confirmed",
            workflow_state="confirmed",
            pending_confirmation=None,
        )

    elif tool_name == "cancel_appointment" and result.get("success"):
        session_memory.update_session(
            session_id,
            booking_status="cancelled",
            workflow_state="idle",
            active_appointment_id=None,
        )

    elif tool_name == "reschedule_appointment" and result.get("success"):
        res = result.get("rescheduled") or {}
        session_memory.update_session(
            session_id,
            active_appointment_id=res.get("id"),
            appointment_time=res.get("new_time"),
            selected_slot=res.get("new_time"),
            booking_status="confirmed",
            workflow_state="confirmed",
        )


async def maybe_set_preferred_doctor(session_id: str, doctor_id: int) -> None:
    sess = session_memory.get_session(session_id)
    if not sess.patient_id:
        return
    async with AsyncSessionLocal() as db:
        await set_preferred_doctor(db, sess.patient_id, doctor_id)


def get_language_instruction(session_id: str, *, compact: bool = False) -> str:
    code = session_memory.get_session(session_id).language_code
    return language_instruction(code, compact=compact)
