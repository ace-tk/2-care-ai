from datetime import datetime, timedelta
from typing import List
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.appointment import Appointment

async def check_availability(db: AsyncSession, doctor_id: int, start_time: datetime, end_time: datetime) -> bool:
    """Check if a doctor is available between start_time and end_time."""
    if start_time < datetime.utcnow():
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

async def suggest_alternative_slots(db: AsyncSession, doctor_id: int, requested_start: datetime, duration_minutes: int = 30) -> List[dict]:
    """Suggest alternative slots if requested slot is booked."""
    suggestions = []
    current_time = requested_start.replace(minute=0, second=0, microsecond=0)
    if current_time < datetime.utcnow():
        current_time = datetime.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        
    end_check_time = current_time + timedelta(days=7)
    
    while current_time < end_check_time and len(suggestions) < 3:
        slot_end = current_time + timedelta(minutes=duration_minutes)
        # Business hours check: 9 AM to 5 PM, Monday to Friday
        if 9 <= current_time.hour < 17 and current_time.weekday() < 5:
            is_avail = await check_availability(db, doctor_id, current_time, slot_end)
            if is_avail:
                suggestions.append({
                    "start": current_time.isoformat(),
                    "end": slot_end.isoformat()
                })
        current_time += timedelta(minutes=30)
        
    return suggestions

async def create_appointment(
    db: AsyncSession, patient_id: int, doctor_id: int, start_time: datetime, end_time: datetime, reason: str
) -> Appointment:
    """Create a new appointment, preventing double booking and past slots."""
    if start_time <= datetime.utcnow():
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
        
    if new_start <= datetime.utcnow():
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
