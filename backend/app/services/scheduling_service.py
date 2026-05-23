"""
Database-backed appointment scheduling for Groq tool execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.services import appointment_service
from app.services.doctor_service import (
    doctor_to_dict,
    get_doctor,
    list_active_doctors,
    search_doctors_by_specialty,
)
from app.services.specialty import extract_specialty_from_text, normalize_specialty

logger = logging.getLogger(__name__)

DEFAULT_PATIENT_ID = 1
SLOT_DURATION_MINUTES = 30


def _parse_slot_time(slot_time: str) -> Optional[datetime]:
    try:
        return datetime.strptime(slot_time, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _slot_end(start: datetime) -> datetime:
    return start + timedelta(minutes=SLOT_DURATION_MINUTES)


def _generate_slots_for_date(doctor: Doctor, date_str: str) -> List[datetime]:
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return []

    now = datetime.now()
    slots: List[datetime] = []
    for time_str in doctor.available_slots or []:
        try:
            hour, minute = map(int, time_str.split(":"))
        except ValueError:
            continue
        slot_start = datetime.combine(
            target_date, datetime.min.time().replace(hour=hour, minute=minute)
        )
        if slot_start > now:
            slots.append(slot_start)
    return sorted(slots)


async def get_available_slots(
    db: AsyncSession, doctor_id: int, date_str: str
) -> dict:
    doctor = await get_doctor(db, doctor_id)
    if not doctor:
        return {"success": False, "error": f"Doctor ID {doctor_id} not found.", "slots": []}

    candidates = _generate_slots_for_date(doctor, date_str)
    available: List[str] = []
    for slot_start in candidates:
        slot_end = _slot_end(slot_start)
        if await appointment_service.check_availability(db, doctor_id, slot_start, slot_end):
            available.append(slot_start.strftime("%Y-%m-%d %H:%M"))

    return {
        "success": True,
        "doctor_id": doctor_id,
        "doctor": doctor.full_name,
        "date": date_str,
        "slots": available,
    }


async def find_nearest_available_slot(
    db: AsyncSession,
    doctor_id: int,
    preferred_time: str,
) -> dict:
    """Find the nearest available slot on or after the preferred time."""
    doctor = await get_doctor(db, doctor_id)
    if not doctor:
        return {"success": False, "error": f"Doctor ID {doctor_id} not found."}

    preferred = _parse_slot_time(preferred_time)
    if not preferred:
        return {
            "success": False,
            "error": "Invalid preferred_time. Use YYYY-MM-DD HH:MM.",
        }

    if preferred <= datetime.now():
        return {
            "success": False,
            "error": "Cannot book a slot in the past.",
            "alternatives": [],
        }

    best_slot: Optional[datetime] = None
    best_delta: Optional[timedelta] = None

    for day_offset in range(8):
        check_date = (preferred.date() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        for slot_start in _generate_slots_for_date(doctor, check_date):
            if slot_start < preferred and day_offset == 0:
                continue
            slot_end = _slot_end(slot_start)
            if not await appointment_service.check_availability(
                db, doctor_id, slot_start, slot_end
            ):
                continue
            delta = abs(slot_start - preferred)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_slot = slot_start

    if not best_slot:
        alts = await _collect_alternatives(db, doctor_id, preferred)
        return {
            "success": False,
            "error": "No available slots found near the requested time.",
            "alternatives": alts,
        }

    return {
        "success": True,
        "doctor_id": doctor_id,
        "doctor": doctor.full_name,
        "slot_time": best_slot.strftime("%Y-%m-%d %H:%M"),
        "exact_match": best_slot == preferred,
    }


async def _collect_alternatives(
    db: AsyncSession, doctor_id: int, requested_start: datetime, limit: int = 5
) -> List[str]:
    alts = await appointment_service.suggest_alternative_slots(
        db, doctor_id, requested_start, duration_minutes=SLOT_DURATION_MINUTES
    )
    return [a["start"].replace("T", " ")[:16] for a in alts[:limit]]


async def resolve_doctor_for_booking(
    db: AsyncSession,
    doctor_id: Optional[int],
    specialty: Optional[str],
    preferred_time: str,
) -> dict:
    """
    Resolve a doctor_id for booking. Picks the doctor with the nearest open slot
    when multiple doctors match the specialty.
    """
    if doctor_id is not None:
        doctor = await get_doctor(db, doctor_id)
        if not doctor:
            return {"success": False, "error": f"Doctor ID {doctor_id} not found."}
        return {"success": True, "doctor_id": doctor.id, "doctor": doctor_to_dict(doctor)}

    canonical = normalize_specialty(specialty or "")
    if not canonical:
        return {
            "success": False,
            "error": "doctor_id or specialty is required to book an appointment.",
        }

    search = await search_doctors_by_specialty(db, canonical)
    if not search.get("success"):
        return search

    preferred = _parse_slot_time(preferred_time)
    if not preferred:
        return {"success": False, "error": "Invalid slot_time. Use YYYY-MM-DD HH:MM."}

    best_doctor: Optional[Doctor] = None
    best_slot: Optional[str] = None
    best_delta: Optional[timedelta] = None

    for doc_summary in search["doctors"]:
        nearest = await find_nearest_available_slot(
            db, doc_summary["id"], preferred_time
        )
        if not nearest.get("success"):
            continue
        slot_dt = _parse_slot_time(nearest["slot_time"])
        if not slot_dt:
            continue
        delta = abs(slot_dt - preferred)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_doctor = await get_doctor(db, doc_summary["id"])
            best_slot = nearest["slot_time"]

    if not best_doctor or not best_slot:
        return {
            "success": False,
            "specialty": canonical,
            "error": search.get(
                "error",
                f"Sorry, no {canonical.lower()}s are currently available.",
            ),
            "suggestion": search.get(
                "suggestion",
                "Would you like another specialty or a different time?",
            ),
        }

    return {
        "success": True,
        "doctor_id": best_doctor.id,
        "doctor": doctor_to_dict(best_doctor),
        "resolved_slot_time": best_slot,
    }


async def book_appointment(
    db: AsyncSession,
    *,
    patient_name: str = "Patient",
    patient_id: int = DEFAULT_PATIENT_ID,
    doctor_id: Optional[int] = None,
    specialty: Optional[str] = None,
    slot_time: str,
    reason: str = "General consultation",
    allow_nearest_slot: bool = True,
) -> dict:
    """
    Book an appointment. Resolves doctor_id from specialty when missing.
    Returns the standard success payload with booking_id.
    """
    resolved_slot = slot_time
    if doctor_id is None:
        resolution = await resolve_doctor_for_booking(
            db,
            None,
            specialty or extract_specialty_from_text(reason),
            slot_time,
        )
        if not resolution.get("success"):
            return {
                "success": False,
                "error": resolution.get("error", "Unable to resolve a doctor."),
                "suggestion": resolution.get("suggestion"),
                "alternatives": resolution.get("alternatives", []),
            }
        doctor_id = resolution["doctor_id"]
        if allow_nearest_slot and resolution.get("resolved_slot_time"):
            resolved_slot = resolution["resolved_slot_time"]

    start = _parse_slot_time(resolved_slot)
    if not start:
        return {
            "success": False,
            "error": "Invalid slot_time. Use YYYY-MM-DD HH:MM.",
            "alternatives": [],
        }

    if start <= datetime.now():
        return {
            "success": False,
            "error": "Cannot book a slot in the past.",
            "alternatives": await _collect_alternatives(db, doctor_id, start),
        }

    end = _slot_end(start)
    doctor = await get_doctor(db, doctor_id)
    if not doctor:
        return {"success": False, "error": f"Doctor ID {doctor_id} not found."}

    is_avail = await appointment_service.check_availability(db, doctor_id, start, end)
    if not is_avail:
        if allow_nearest_slot:
            nearest = await find_nearest_available_slot(db, doctor_id, resolved_slot)
            if nearest.get("success") and nearest.get("slot_time") != resolved_slot:
                return await book_appointment(
                    db,
                    patient_name=patient_name,
                    patient_id=patient_id,
                    doctor_id=doctor_id,
                    slot_time=nearest["slot_time"],
                    reason=reason,
                    allow_nearest_slot=False,
                )
        alts = await _collect_alternatives(db, doctor_id, start)
        return {
            "success": False,
            "error": f"Time slot {resolved_slot} is not available.",
            "alternatives": alts,
        }

    try:
        appt = await appointment_service.create_appointment(
            db, patient_id, doctor_id, start, end, reason
        )
    except ValueError as exc:
        alts = await _collect_alternatives(db, doctor_id, start)
        return {
            "success": False,
            "error": str(exc),
            "alternatives": alts,
        }

    time_str = start.strftime("%Y-%m-%d %H:%M")
    booking_id = f"APT-{appt.id}"
    logger.info(
        "[Scheduling] Booked %s | %s | %s | patient=%s",
        booking_id,
        doctor.full_name,
        time_str,
        patient_name,
    )
    return {
        "success": True,
        "doctor": doctor.full_name,
        "specialty": doctor.specialty,
        "time": time_str,
        "booking_id": booking_id,
        "appointment": {
            "id": appt.id,
            "booking_id": booking_id,
            "doctor": doctor.full_name,
            "specialty": doctor.specialty,
            "time": time_str,
            "patient_name": patient_name,
            "reason": reason,
            "status": appt.status,
        },
    }


async def cancel_appointment(db: AsyncSession, appointment_id: int) -> dict:
    try:
        appt = await appointment_service.cancel_appointment(db, appointment_id)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    doctor = await get_doctor(db, appt.doctor_id)
    return {
        "success": True,
        "cancelled": {
            "id": appt.id,
            "booking_id": f"APT-{appt.id}",
            "doctor": doctor.full_name if doctor else "Unknown",
            "time": appt.scheduled_start.strftime("%Y-%m-%d %H:%M"),
        },
    }


async def reschedule_appointment(
    db: AsyncSession, appointment_id: int, new_slot_time: str
) -> dict:
    new_start = _parse_slot_time(new_slot_time)
    if not new_start:
        return {"success": False, "error": "Invalid time format. Use YYYY-MM-DD HH:MM."}

    new_end = _slot_end(new_start)
    try:
        appt = await appointment_service.reschedule_appointment(
            db, appointment_id, new_start, new_end
        )
    except ValueError as exc:
        doctor = None
        existing = await db.get(Appointment, appointment_id)
        alts: List[str] = []
        if existing:
            alts = await _collect_alternatives(db, existing.doctor_id, new_start)
        return {"success": False, "error": str(exc), "alternatives": alts}

    doctor = await get_doctor(db, appt.doctor_id)
    return {
        "success": True,
        "rescheduled": {
            "id": appt.id,
            "booking_id": f"APT-{appt.id}",
            "doctor": doctor.full_name if doctor else "Unknown",
            "new_time": new_slot_time,
        },
    }


async def get_patient_appointments(db: AsyncSession, patient_id: int = DEFAULT_PATIENT_ID) -> dict:
    from sqlalchemy import select

    result = await db.execute(
        select(Appointment)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status != "cancelled",
        )
        .order_by(Appointment.scheduled_start)
    )
    rows = []
    for appt in result.scalars().all():
        doctor = await get_doctor(db, appt.doctor_id)
        rows.append(
            {
                "id": appt.id,
                "booking_id": f"APT-{appt.id}",
                "doctor": doctor.full_name if doctor else "Unknown",
                "specialty": doctor.specialty if doctor else None,
                "time": appt.scheduled_start.strftime("%Y-%m-%d %H:%M"),
                "reason": appt.reason,
                "status": appt.status,
            }
        )
    return {"success": True, "appointments": rows}


async def list_doctors(db: AsyncSession) -> dict:
    doctors = await list_active_doctors(db)
    return {"success": True, "doctors": doctors, "count": len(doctors)}
