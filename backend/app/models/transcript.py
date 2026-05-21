from sqlalchemy import String, Text, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone
from backend.app.core.database import Base


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    
    audio_url: Mapped[str] = mapped_column(String(512), nullable=True)
    detected_language: Mapped[str] = mapped_column(String(10), default="en")  # Detected language (e.g., es, zh)
    original_text: Mapped[str] = mapped_column(Text, nullable=True)  # Raw transcription
    translated_text: Mapped[str] = mapped_column(Text, nullable=True)  # English translation (if applicable)
    clinical_summary: Mapped[str] = mapped_column(Text, nullable=True)  # Structured SOAP note or summaries
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    patient: Mapped["Patient"] = relationship("Patient", back_populates="transcripts")
    creator: Mapped["User"] = relationship("User", back_populates="transcripts")
