"""
Database seed data for doctors, multilingual patients, and demo appointments.
Idempotent — upserts by email / MRN on every startup.
"""

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

logger = logging.getLogger(__name__)

DEFAULT_SLOT_HOURS = [
    "09:00",
    "09:30",
    "10:00",
    "10:30",
    "11:00",
    "14:00",
    "14:30",
    "15:00",
    "15:30",
    "16:00",
    "16:30",
    "17:00",
]

DOCTOR_SEEDS = [
    {
        "first_name": "Sarah",
        "last_name": "Jenkins",
        "email": "sarah.jenkins@2care.ai",
        "specialty": "Dentist",
        "available_slots": DEFAULT_SLOT_HOURS,
        "languages": ["english", "hindi"],
    },
    {
        "first_name": "Raj",
        "last_name": "Mehta",
        "email": "raj.mehta@2care.ai",
        "specialty": "Cardiologist",
        "available_slots": DEFAULT_SLOT_HOURS,
        "languages": ["english", "hindi", "tamil"],
    },
    {
        "first_name": "Priya",
        "last_name": "Nair",
        "email": "priya.nair@2care.ai",
        "specialty": "Dermatologist",
        "available_slots": DEFAULT_SLOT_HOURS,
        "languages": ["english", "tamil"],
    },
    {
        "first_name": "Arjun",
        "last_name": "Rao",
        "email": "arjun.rao@2care.ai",
        "specialty": "General Physician",
        "available_slots": DEFAULT_SLOT_HOURS,
        "languages": ["english", "hindi", "tamil"],
    },
]

PATIENT_SEEDS = [
    {
        "medical_record_number": "DEMO-001",
        "first_name": "Elena",
        "last_name": "Gomez",
        "date_of_birth": date(1978, 4, 12),
        "language_preference": "en",
    },
    {
        "medical_record_number": "DEMO-HI-001",
        "first_name": "Amit",
        "last_name": "Sharma",
        "date_of_birth": date(1985, 6, 15),
        "language_preference": "hi",
    },
    {
        "medical_record_number": "DEMO-TA-001",
        "first_name": "Lakshmi",
        "last_name": "Venkatesh",
        "date_of_birth": date(1992, 3, 22),
        "language_preference": "ta",
    },
]


async def seed_doctors(db: AsyncSession) -> int:
    touched = 0
    for row in DOCTOR_SEEDS:
        result = await db.execute(select(Doctor).where(Doctor.email == row["email"]))
        doctor = result.scalar_one_or_none()
        if doctor:
            for key, value in row.items():
                setattr(doctor, key, value)
        else:
            db.add(Doctor(**row))
        touched += 1
    await db.commit()
    logger.info("[Seed] Doctor roster upserted (%s records)", touched)
    return touched


async def _upsert_patient(db: AsyncSession, row: dict) -> Patient:
    result = await db.execute(
        select(Patient).where(Patient.medical_record_number == row["medical_record_number"])
    )
    patient = result.scalar_one_or_none()
    if patient:
        for key, value in row.items():
            setattr(patient, key, value)
    else:
        patient = Patient(**row, is_active=True)
        db.add(patient)
    await db.commit()
    await db.refresh(patient)
    return patient


async def seed_patients(db: AsyncSession) -> list[Patient]:
    patients = []
    for row in PATIENT_SEEDS:
        patients.append(await _upsert_patient(db, row))
    logger.info("[Seed] Patients upserted (%s)", len(patients))
    return patients


async def seed_demo_appointments(db: AsyncSession) -> int:
    """Upcoming appointments for campaign / booking demos."""
    result = await db.execute(select(Doctor).where(Doctor.email == "sarah.jenkins@2care.ai"))
    dentist = result.scalar_one_or_none()
    if not dentist:
        return 0

    patients_result = await db.execute(select(Patient))
    patients = {p.medical_record_number: p for p in patients_result.scalars().all()}
    count = 0
    now = datetime.utcnow()

    specs = [
        ("DEMO-001", 1, "General checkup"),
        ("DEMO-HI-001", 2, "दंत जांच"),
        ("DEMO-TA-001", 3, "தோல் பரிசோதனை"),
    ]
    for mrn, day_offset, reason in specs:
        patient = patients.get(mrn)
        if not patient:
            continue
        start = now + timedelta(days=day_offset, hours=10)
        existing = await db.execute(
            select(Appointment).where(
                Appointment.patient_id == patient.id,
                Appointment.scheduled_start == start,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            Appointment(
                patient_id=patient.id,
                doctor_id=dentist.id if day_offset == 1 else dentist.id,
                scheduled_start=start,
                scheduled_end=start + timedelta(minutes=30),
                reason=reason,
                status="scheduled",
            )
        )
        count += 1

    await db.commit()
    logger.info("[Seed] Demo appointments created (%s)", count)
    return count


async def run_startup_seed(db: AsyncSession) -> None:
    await seed_doctors(db)
    await seed_patients(db)
    await seed_demo_appointments(db)
