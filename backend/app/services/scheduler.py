"""
Legacy scheduling module — delegates to database-backed scheduling_service.

Kept so existing imports (`from app.services import scheduler`) continue to work.
"""

from app.services.scheduling_service import (  # noqa: F401
    book_appointment,
    cancel_appointment,
    find_nearest_available_slot,
    get_available_slots,
    get_patient_appointments,
    list_doctors,
    reschedule_appointment,
)
