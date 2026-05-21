from sqlalchemy import String, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import date
from backend.app.core.database import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    medical_record_number: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    language_preference: Mapped[str] = mapped_column(String(10), default="en")  # en, es, fr, zh, etc.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationship to audio transcripts of consultations
    transcripts: Mapped[list["Transcript"]] = relationship(
        "Transcript", back_populates="patient", cascade="all, delete-orphan"
    )
