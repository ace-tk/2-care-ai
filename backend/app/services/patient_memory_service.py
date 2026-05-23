"""
patient_memory_service.py
-------------------------
Persistent patient memory: language preference, appointments, preferred doctor,
and interaction summaries stored in the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.patient import Patient
from app.services.doctor_service import get_doctor
from app.services.language_service import normalize_language_code

logger = logging.getLogger(__name__)


@dataclass
class PatientMemorySnapshot:
    patient_id: int
    full_name: str
    language_preference: str = "en"
    preferred_doctor_id: Optional[int] = None
    preferred_doctor_name: Optional[str] = None
    last_interaction_summary: Optional[str] = None
    recent_appointments: List[dict] = field(default_factory=list)

    def to_prompt_block(self, *, compact: bool = False) -> str:
        if compact:
            parts = [f"{self.full_name} (id={self.patient_id}, lang={self.language_preference})"]
            if self.preferred_doctor_name:
                parts.append(f"pref_dr={self.preferred_doctor_name}")
            return " | ".join(parts)
        lines = [
            f"Patient: {self.full_name} (ID {self.patient_id})",
            f"Stored language preference: {self.language_preference}",
        ]
        if self.preferred_doctor_name:
            lines.append(f"Preferred doctor: {self.preferred_doctor_name}")
        if self.last_interaction_summary:
            lines.append(f"Last visit summary: {self.last_interaction_summary[:200]}")
        if self.recent_appointments:
            lines.append("Recent appointments:")
            for appt in self.recent_appointments[:3]:
                lines.append(
                    f"  - {appt.get('booking_id')} | {appt.get('doctor')} | "
                    f"{appt.get('time')} | {appt.get('status')}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "full_name": self.full_name,
            "language_preference": self.language_preference,
            "preferred_doctor_id": self.preferred_doctor_id,
            "preferred_doctor_name": self.preferred_doctor_name,
            "last_interaction_summary": self.last_interaction_summary,
            "recent_appointments": self.recent_appointments,
        }


async def load_patient_memory(db: AsyncSession, patient_id: int) -> Optional[PatientMemorySnapshot]:
    patient = await db.get(Patient, patient_id)
    if not patient:
        return None

    preferred_name = None
    if patient.preferred_doctor_id:
        doctor = await get_doctor(db, patient.preferred_doctor_id)
        preferred_name = doctor.full_name if doctor else None

    result = await db.execute(
        select(Appointment)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status != "cancelled",
        )
        .order_by(Appointment.scheduled_start.desc())
        .limit(8)
    )

    appointments: List[dict] = []
    for appt in result.scalars().all():
        doctor = await get_doctor(db, appt.doctor_id)
        appointments.append(
            {
                "id": appt.id,
                "booking_id": f"APT-{appt.id}",
                "doctor": doctor.full_name if doctor else "Unknown",
                "specialty": doctor.specialty if doctor else None,
                "doctor_id": appt.doctor_id,
                "time": appt.scheduled_start.strftime("%Y-%m-%d %H:%M"),
                "status": appt.status,
                "reason": appt.reason,
            }
        )

    return PatientMemorySnapshot(
        patient_id=patient.id,
        full_name=f"{patient.first_name} {patient.last_name}",
        language_preference=normalize_language_code(patient.language_preference),
        preferred_doctor_id=patient.preferred_doctor_id,
        preferred_doctor_name=preferred_name,
        last_interaction_summary=patient.last_interaction_summary,
        recent_appointments=appointments,
    )


async def update_language_preference(
    db: AsyncSession, patient_id: int, language_code: str
) -> None:
    patient = await db.get(Patient, patient_id)
    if not patient:
        return
    code = normalize_language_code(language_code)
    if patient.language_preference != code:
        patient.language_preference = code
        await db.commit()
        logger.info("[PatientMemory] Updated language preference | patient=%s | lang=%s", patient_id, code)


async def update_last_interaction_summary(
    db: AsyncSession,
    patient_id: int,
    user_text: str,
    assistant_text: str,
    intent: str,
) -> str:
    """Persist a concise rolling summary (no external LLM — deterministic)."""
    patient = await db.get(Patient, patient_id)
    if not patient:
        return ""

    snippet_user = (user_text or "")[:120].replace("\n", " ")
    snippet_ai = (assistant_text or "")[:160].replace("\n", " ")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    summary = (
        f"[{timestamp}] intent={intent}; patient said: {snippet_user}; "
        f"assistant: {snippet_ai}"
    )
    patient.last_interaction_summary = summary[:2000]
    await db.commit()
    return summary


async def set_preferred_doctor(
    db: AsyncSession, patient_id: int, doctor_id: int
) -> None:
    patient = await db.get(Patient, patient_id)
    if not patient:
        return
    patient.preferred_doctor_id = doctor_id
    await db.commit()
    logger.info(
        "[PatientMemory] Preferred doctor set | patient=%s | doctor=%s",
        patient_id,
        doctor_id,
    )
