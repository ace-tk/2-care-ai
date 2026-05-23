"""Doctor lookup and serialization for scheduling."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.doctor import Doctor
from app.services.specialty import normalize_specialty, specialty_matches


def doctor_to_dict(doctor: Doctor) -> dict:
    return {
        "id": doctor.id,
        "name": doctor.full_name,
        "specialty": doctor.specialty,
        "available_slots": doctor.available_slots or [],
        "languages": doctor.languages or [],
    }


async def list_active_doctors(db: AsyncSession) -> List[dict]:
    result = await db.execute(
        select(Doctor).where(Doctor.is_active.is_(True)).order_by(Doctor.id)
    )
    return [doctor_to_dict(d) for d in result.scalars().all()]


async def get_doctor(db: AsyncSession, doctor_id: int) -> Optional[Doctor]:
    return await db.get(Doctor, doctor_id)


async def search_doctors_by_specialty(
    db: AsyncSession, specialty_query: str
) -> dict:
    """
    Search doctors by specialty alias or canonical name.

    Returns:
        { "success": True, "specialty": "...", "doctors": [...], "count": N }
        or { "success": False, "error": "...", "suggestion": "..." }
    """
    canonical = normalize_specialty(specialty_query)
    if not canonical:
        return {
            "success": False,
            "error": f"Could not resolve specialty from '{specialty_query}'.",
            "suggestion": "Try Dentist, Cardiologist, Dermatologist, or General Physician.",
        }

    result = await db.execute(select(Doctor).where(Doctor.is_active.is_(True)))
    doctors = [
        d for d in result.scalars().all() if specialty_matches(d.specialty, canonical)
    ]

    if not doctors:
        return {
            "success": False,
            "specialty": canonical,
            "error": f"Sorry, no {canonical.lower()}s are currently available.",
            "suggestion": "Would you like another specialty or a different time?",
            "doctors": [],
            "count": 0,
        }

    return {
        "success": True,
        "specialty": canonical,
        "doctors": [doctor_to_dict(d) for d in doctors],
        "count": len(doctors),
    }
