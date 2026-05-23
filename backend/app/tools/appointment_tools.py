from langchain_core.tools import tool
from datetime import datetime
from backend.app.core.database import AsyncSessionLocal
from backend.app.services.appointment_service import (
    check_availability,
    suggest_alternative_slots,
    create_appointment,
    cancel_appointment,
    reschedule_appointment
)

@tool
async def check_availability_tool(doctor_id: int, start_time_iso: str, end_time_iso: str) -> str:
    """Check if a doctor is available for a specific time slot."""
    start_time = datetime.fromisoformat(start_time_iso)
    end_time = datetime.fromisoformat(end_time_iso)
    async with AsyncSessionLocal() as db:
        is_avail = await check_availability(db, doctor_id, start_time, end_time)
        if is_avail:
            return "Doctor is available."
        else:
            alts = await suggest_alternative_slots(db, doctor_id, start_time)
            if not alts:
                return "Doctor is not available. No alternative slots found within the next 7 days."
            alt_str = ", ".join([f"{a['start']} to {a['end']}" for a in alts])
            return f"Doctor is not available. Suggested alternative slots: {alt_str}"

@tool
async def book_appointment_tool(patient_id: int, doctor_id: int, start_time_iso: str, end_time_iso: str, reason: str) -> str:
    """Book a new appointment."""
    start_time = datetime.fromisoformat(start_time_iso)
    end_time = datetime.fromisoformat(end_time_iso)
    async with AsyncSessionLocal() as db:
        try:
            appt = await create_appointment(db, patient_id, doctor_id, start_time, end_time, reason)
            return f"Appointment booked successfully. ID: {appt.id}"
        except ValueError as e:
            return f"Failed to book appointment: {str(e)}"
        except Exception as e:
            return f"An error occurred: {str(e)}"

@tool
async def cancel_appointment_tool(appointment_id: int) -> str:
    """Cancel an existing appointment."""
    async with AsyncSessionLocal() as db:
        try:
            appt = await cancel_appointment(db, appointment_id)
            return f"Appointment {appt.id} cancelled successfully."
        except ValueError as e:
            return f"Failed to cancel appointment: {str(e)}"
        except Exception as e:
            return f"An error occurred: {str(e)}"

@tool
async def reschedule_appointment_tool(appointment_id: int, new_start_iso: str, new_end_iso: str) -> str:
    """Reschedule an existing appointment."""
    new_start = datetime.fromisoformat(new_start_iso)
    new_end = datetime.fromisoformat(new_end_iso)
    async with AsyncSessionLocal() as db:
        try:
            appt = await reschedule_appointment(db, appointment_id, new_start, new_end)
            return f"Appointment {appt.id} rescheduled successfully to {new_start_iso}."
        except ValueError as e:
            return f"Failed to reschedule appointment: {str(e)}"
        except Exception as e:
            return f"An error occurred: {str(e)}"
