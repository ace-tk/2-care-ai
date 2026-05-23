"""Outbound campaign execution logs."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CampaignLog(Base):
    __tablename__ = "campaign_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True, nullable=False)
    appointment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("appointments.id"), nullable=True, index=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    campaign_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="queued"
    )  # queued, processing, active, dispatched, completed, failed
    preferred_language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, default="en")
    message_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outbound_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    patient_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    trace_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
