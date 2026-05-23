from sqlalchemy import String, Boolean, Date, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import date
from typing import Optional
from app.core.database import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    medical_record_number: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    language_preference: Mapped[str] = mapped_column(String(10), default="en")  # en, hi, ta
    preferred_doctor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("doctors.id"), nullable=True, index=True
    )
    last_interaction_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships to audio transcripts of consultations
    transcripts: Mapped[list["Transcript"]] = relationship(
        "Transcript", back_populates="patient", cascade="all, delete-orphan"
    )

    # Relationship to appointments
    appointments: Mapped[list["Appointment"]] = relationship(
        "Appointment", back_populates="patient", cascade="all, delete-orphan"
    )
