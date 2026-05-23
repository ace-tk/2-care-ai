"""Pydantic schemas for outbound campaigns."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CampaignBaseRequest(BaseModel):
    patient_id: int
    scheduled_time: Optional[datetime] = Field(
        None, description="When to run the campaign (UTC). Defaults to now."
    )
    preferred_language: str = Field(default="en", description="en | hi | ta")
    message_template: Optional[str] = Field(
        None, description="Optional override for outbound opener text"
    )
    appointment_id: Optional[int] = None


class ReminderCampaignRequest(CampaignBaseRequest):
    """Schedule an appointment reminder campaign."""


class FollowupCampaignRequest(CampaignBaseRequest):
    """Schedule a post-operative / follow-up campaign."""


class CampaignResponse(BaseModel):
    id: int
    patient_id: int
    appointment_id: Optional[int] = None
    session_id: Optional[str] = None
    campaign_type: str
    status: str
    preferred_language: Optional[str] = None
    message_template: Optional[str] = None
    outbound_message: Optional[str] = None
    patient_response: Optional[str] = None
    result: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3
    latency_ms: Optional[float] = None
    trace_summary: Optional[str] = None

    class Config:
        from_attributes = True


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignResponse]
    total: int
