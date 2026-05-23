from backend.app.core.database import Base
from backend.app.models.doctor import Doctor
from backend.app.models.appointment import Appointment
from backend.app.models.patient import Patient
from backend.app.models.transcript import Transcript

__all__ = ["Base", "User", "Patient", "Doctor", "Appointment", "Transcript"]
