from datetime import datetime, timedelta
from typing import List
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.appointment import Appointment


def _now() -> datetime:
    """Local wall-clock time for patient-facing scheduling."""
    return datetime.now().replace(microsecond=0)


async def check_availability(db: AsyncSession, doctor_id: int, start_time: datetime, end_time: datetime) -> bool:
    """Check if a doctor is available between start_time and end_time."""
    if start_time <= _now():
        return False
        
    query = select(Appointment).where(
        Appointment.doctor_id == doctor_id,
        Appointment.status != "cancelled",
        or_(
            and_(Appointment.scheduled_start <= start_time, Appointment.scheduled_end > start_time),
            and_(Appointment.scheduled_start < end_time, Appointment.scheduled_end >= end_time),
            and_(Appointment.scheduled_start >= start_time, Appointment.scheduled_end <= end_time)
        )
    )
    result = await db.execute(query)
    conflicts = result.scalars().all()
    return len(conflicts) == 0

async def suggest_alternative_slots(
    db: AsyncSession,
    doctor_id: int,
    requested_start: datetime,
    duration_minutes: int = 30,
) -> List[dict]:
    """Suggest alternative slots using the doctor's weekly slot template."""
    from app.models.doctor import Doctor

    doctor = await db.get(Doctor, doctor_id)
    if not doctor or not doctor.available_slots:
        return []

    suggestions: List[dict] = []
    for day_offset in range(8):
        if len(suggestions) >= 5:
            break
        check_date = requested_start.date() + timedelta(days=day_offset)
        for time_str in doctor.available_slots:
            try:
                hour, minute = map(int, time_str.split(":"))
            except ValueError:
                continue
            slot_start = datetime.combine(
                check_date, datetime.min.time().replace(hour=hour, minute=minute)
            )
            if slot_start <= _now():
                continue
            slot_end = slot_start + timedelta(minutes=duration_minutes)
            if await check_availability(db, doctor_id, slot_start, slot_end):
                suggestions.append(
                    {
                        "start": slot_start.strftime("%Y-%m-%d %H:%M"),
                        "end": slot_end.strftime("%Y-%m-%d %H:%M"),
                    }
                )
            if len(suggestions) >= 5:
                break
    return suggestions

async def create_appointment(
    db: AsyncSession, patient_id: int, doctor_id: int, start_time: datetime, end_time: datetime, reason: str
) -> Appointment:
    """Create a new appointment, preventing double booking and past slots."""
    if start_time <= _now():
        raise ValueError("Cannot book appointments in the past.")
        
    is_avail = await check_availability(db, doctor_id, start_time, end_time)
    if not is_avail:
        raise ValueError("Doctor is not available at the requested time.")
        
    appointment = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        scheduled_start=start_time,
        scheduled_end=end_time,
        reason=reason,
        status="scheduled"
    )
    db.add(appointment)
    await db.commit()
    await db.refresh(appointment)
    return appointment

async def cancel_appointment(db: AsyncSession, appointment_id: int) -> Appointment:
    """Cancel an existing appointment."""
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise ValueError("Appointment not found.")
        
    appointment.status = "cancelled"
    await db.commit()
    await db.refresh(appointment)
    return appointment

async def reschedule_appointment(
    db: AsyncSession, appointment_id: int, new_start: datetime, new_end: datetime
) -> Appointment:
    """Reschedule an existing appointment preventing double booking."""
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise ValueError("Appointment not found.")
        
    if new_start <= _now():
        raise ValueError("Cannot reschedule to the past.")
        
    # Check availability excluding the current appointment
    query = select(Appointment).where(
        Appointment.doctor_id == appointment.doctor_id,
        Appointment.id != appointment_id,
        Appointment.status != "cancelled",
        or_(
            and_(Appointment.scheduled_start <= new_start, Appointment.scheduled_end > new_start),
            and_(Appointment.scheduled_start < new_end, Appointment.scheduled_end >= new_end),
            and_(Appointment.scheduled_start >= new_start, Appointment.scheduled_end <= new_end)
        )
    )
    result = await db.execute(query)
    if len(result.scalars().all()) > 0:
        raise ValueError("Doctor is not available at the new requested time.")
        
    appointment.scheduled_start = new_start
    appointment.scheduled_end = new_end
    await db.commit()
    await db.refresh(appointment)
    return appointment
