"""REST endpoints for outbound campaigns."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.campaign import (
    CampaignListResponse,
    CampaignResponse,
    FollowupCampaignRequest,
    ReminderCampaignRequest,
)
from app.services.campaign_service import campaign_service, normalize_campaign_type

router = APIRouter()
logger = logging.getLogger(__name__)


def _to_response(log) -> CampaignResponse:
    return CampaignResponse.model_validate(log)


@router.post("/reminder", response_model=CampaignResponse)
async def schedule_reminder(
    body: ReminderCampaignRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Schedule an appointment reminder campaign.

    Payload: patient_id, scheduled_time, preferred_language, message_template, appointment_id.
    """
    try:
        log = await campaign_service.schedule_campaign(
            db,
            patient_id=body.patient_id,
            campaign_type="reminder",
            appointment_id=body.appointment_id,
            scheduled_time=body.scheduled_time or datetime.utcnow(),
            preferred_language=body.preferred_language,
            message_template=body.message_template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("[CAMPAIGN API] Reminder scheduled | id=%s", log.id)
    return _to_response(log)


@router.post("/followup", response_model=CampaignResponse)
async def schedule_followup(
    body: FollowupCampaignRequest,
    db: AsyncSession = Depends(get_db),
):
    """Schedule a post-operative / follow-up outbound campaign."""
    try:
        log = await campaign_service.schedule_campaign(
            db,
            patient_id=body.patient_id,
            campaign_type="followup",
            appointment_id=body.appointment_id,
            scheduled_time=body.scheduled_time or datetime.utcnow(),
            preferred_language=body.preferred_language,
            message_template=body.message_template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info("[CAMPAIGN API] Follow-up scheduled | id=%s", log.id)
    return _to_response(log)


@router.get("", response_model=CampaignListResponse)
async def list_campaigns(
    patient_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List outbound campaign history with status and metadata."""
    rows = await campaign_service.list_campaigns(db, patient_id=patient_id, limit=limit)
    return CampaignListResponse(
        campaigns=[_to_response(r) for r in rows],
        total=len(rows),
    )


class TriggerCampaignRequest(ReminderCampaignRequest):
    campaign_type: str = "reminder"


# Legacy aliases
@router.post("/trigger", response_model=CampaignResponse)
async def trigger_campaign_legacy(
    body: TriggerCampaignRequest,
    db: AsyncSession = Depends(get_db),
):
    """Legacy trigger endpoint — schedules campaign (use /reminder or /followup)."""
    ctype = normalize_campaign_type(body.campaign_type)
    try:
        log = await campaign_service.schedule_campaign(
            db,
            patient_id=body.patient_id,
            campaign_type=ctype,
            appointment_id=body.appointment_id,
            scheduled_time=body.scheduled_time,
            preferred_language=body.preferred_language,
            message_template=body.message_template,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(log)


@router.get("/logs", response_model=CampaignListResponse)
async def list_campaign_logs_legacy(
    patient_id: Optional[int] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Legacy logs endpoint — same as GET /campaigns."""
    rows = await campaign_service.list_campaigns(db, patient_id=patient_id, limit=limit)
    return CampaignListResponse(
        campaigns=[_to_response(r) for r in rows],
        total=len(rows),
    )


@router.post("/demo/tomorrow-reminder", response_model=CampaignResponse)
async def demo_tomorrow_reminder(
    patient_id: int = Query(1),
    db: AsyncSession = Depends(get_db),
):
    """Demo: schedule reminder for tomorrow 10:00 UTC."""
    run_at = datetime.utcnow().replace(
        hour=10, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    try:
        log = await campaign_service.schedule_campaign(
            db,
            patient_id=patient_id,
            campaign_type="reminder",
            scheduled_time=run_at,
            preferred_language="en",
            message_template=None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(log)


@router.post("/demo/post-op-followup", response_model=CampaignResponse)
async def demo_post_op_followup(
    patient_id: int = Query(1),
    db: AsyncSession = Depends(get_db),
):
    """Demo: schedule post-op follow-up for immediate dispatch."""
    try:
        log = await campaign_service.schedule_campaign(
            db,
            patient_id=patient_id,
            campaign_type="followup",
            scheduled_time=datetime.utcnow(),
            preferred_language="en",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_response(log)
