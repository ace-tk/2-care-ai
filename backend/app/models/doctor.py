from sqlalchemy import String, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=True)
    specialty: Mapped[str] = mapped_column(String(100), nullable=True)
    # Weekly slot template as "HH:MM" strings (30-minute slots)
    available_slots: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Supported languages, e.g. ["english", "hindi", "tamil"]
    languages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationship to appointments
    appointments: Mapped[list["Appointment"]] = relationship(
        "Appointment", back_populates="doctor", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return f"Dr. {self.first_name} {self.last_name}".strip()
