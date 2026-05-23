"""
session_memory.py
------------------
Short-term conversational memory per WebSocket / voice session.

Tracks workflow state so follow-ups like "Actually make it 6 PM" retain doctor,
date, and booking context.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionContext:
    session_id: str
    patient_id: Optional[int] = None

    # Language
    language_code: str = "en"
    language_confidence: float = 1.0
    language_source: str = "default"

    # Intent & workflow
    last_intent: str = "general"
    workflow_state: str = "idle"
    pending_confirmation: Optional[str] = None

    # Scheduling selections
    active_appointment_id: Optional[int] = None
    selected_doctor_id: Optional[int] = None
    selected_doctor_name: Optional[str] = None
    requested_specialty: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    selected_slot: Optional[str] = None
    offered_slots: List[str] = field(default_factory=list)
    booking_status: str = "none"

    # Conversation continuity
    previous_assistant_response: Optional[str] = None
    last_user_message: Optional[str] = None

    # Outbound campaign
    active_campaign_type: Optional[str] = None
    active_campaign_log_id: Optional[int] = None

    # Legacy alias used in prompts
    @property
    def language_preference(self) -> str:
        return {"en": "english", "hi": "hindi", "ta": "tamil"}.get(
            self.language_code, "english"
        )

    @property
    def doctor_type(self) -> Optional[str]:
        return self.selected_doctor_name or self.requested_specialty

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "patient_id": self.patient_id,
            "language_code": self.language_code,
            "language_confidence": self.language_confidence,
            "language_source": self.language_source,
            "last_intent": self.last_intent,
            "workflow_state": self.workflow_state,
            "pending_confirmation": self.pending_confirmation,
            "active_appointment_id": self.active_appointment_id,
            "selected_doctor_id": self.selected_doctor_id,
            "selected_doctor_name": self.selected_doctor_name,
            "requested_specialty": self.requested_specialty,
            "appointment_date": self.appointment_date,
            "appointment_time": self.appointment_time,
            "selected_slot": self.selected_slot,
            "offered_slots": list(self.offered_slots),
            "booking_status": self.booking_status,
            "previous_assistant_response": self.previous_assistant_response,
            "last_user_message": self.last_user_message,
            "active_campaign_type": self.active_campaign_type,
            "active_campaign_log_id": self.active_campaign_log_id,
        }

    def summary_for_trace(self) -> str:
        parts = [
            f"workflow={self.workflow_state}",
            f"intent={self.last_intent}",
            f"lang={self.language_code}",
        ]
        if self.selected_doctor_name:
            parts.append(f"doctor={self.selected_doctor_name}")
        if self.selected_slot:
            parts.append(f"slot={self.selected_slot}")
        elif self.appointment_time:
            parts.append(f"time={self.appointment_time}")
        if self.active_appointment_id:
            parts.append(f"appt_id={self.active_appointment_id}")
        if self.pending_confirmation:
            parts.append(f"pending={self.pending_confirmation[:40]}")
        return " | ".join(parts)


_sessions: Dict[str, SessionContext] = {}


def get_session(session_id: str) -> SessionContext:
    if session_id not in _sessions:
        _sessions[session_id] = SessionContext(session_id=session_id)
    return _sessions[session_id]


def update_session(session_id: str, **kwargs) -> SessionContext:
    sess = get_session(session_id)
    for key, value in kwargs.items():
        if hasattr(sess, key):
            setattr(sess, key, value)
    return sess


def clear_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
