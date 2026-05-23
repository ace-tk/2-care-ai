from sqlalchemy import String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.core.database import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), nullable=False, index=True)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="scheduled")  # e.g., scheduled, completed, cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    patient: Mapped["Patient"] = relationship("Patient", back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="appointments")
