"""
Background scheduler for outbound campaigns.

Polls DB for due campaigns, generates multilingual outbound messages,
retries on failure, and logs latency metrics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.campaign_log import CampaignLog
from app.services.campaign_service import campaign_service

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 30
RETRY_DELAY_SEC = 120


class CampaignScheduler:
    """Async background worker for scheduled outbound campaigns."""

    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[CAMPAIGN SCHEDULER] Started (poll=%ss)", POLL_INTERVAL_SEC)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[CAMPAIGN SCHEDULER] Stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                async with AsyncSessionLocal() as db:
                    queued = await campaign_service.scan_and_queue_reminders(db)
                    if queued:
                        logger.info("[CAMPAIGN SCHEDULER] Auto-queued %s reminders", queued)
                    processed = await self.process_due_campaigns(db)
                    if processed:
                        logger.info("[CAMPAIGN SCHEDULER] Processed %s due campaigns", processed)
            except Exception as exc:
                logger.error("[CAMPAIGN SCHEDULER] Loop error: %s", exc, exc_info=True)
            await asyncio.sleep(POLL_INTERVAL_SEC)

    async def process_due_campaigns(self, db: AsyncSession) -> int:
        """Execute all queued campaigns whose scheduled_at has passed."""
        now = datetime.utcnow()
        result = await db.execute(
            select(CampaignLog)
            .where(
                CampaignLog.status == "queued",
                CampaignLog.scheduled_at <= now,
            )
            .order_by(CampaignLog.scheduled_at)
            .limit(20)
        )
        count = 0
        for log in result.scalars().all():
            if log.retry_count >= log.max_retries:
                log.status = "failed"
                log.last_error = "max_retries_exceeded"
                log.completed_at = now
                await db.commit()
                continue
            ok = await self.execute_campaign(db, log)
            if ok:
                count += 1
        return count

    async def execute_campaign(self, db: AsyncSession, log: CampaignLog) -> bool:
        """Simulate outbound call: generate AI message, persist log, mark dispatched."""
        t0 = time.perf_counter()
        log.status = "processing"
        log.started_at = datetime.utcnow()
        await db.commit()

        try:
            outbound = await campaign_service.generate_outbound_message(
                db,
                patient_id=log.patient_id,
                campaign_type=log.campaign_type,
                appointment_id=log.appointment_id,
                preferred_language=log.preferred_language or "en",
                message_template=log.message_template,
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            log.outbound_message = outbound["message"]
            log.latency_ms = latency_ms
            log.status = "dispatched"
            log.trace_summary = (
                f"[OUTBOUND TRIGGERED] type={log.campaign_type} lang={outbound['language']} "
                f"latency={latency_ms:.0f}ms"
            )
            log.completed_at = None  # completed when patient responds via WS
            await db.commit()

            logger.info(
                "[CAMPAIGN SCHEDULER] Outbound dispatched | id=%s | type=%s | patient=%s | latency=%.0fms",
                log.id,
                log.campaign_type,
                log.patient_id,
                latency_ms,
            )
            return True
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            log.retry_count += 1
            log.latency_ms = latency_ms
            log.last_error = str(exc)[:500]
            if log.retry_count >= log.max_retries:
                log.status = "failed"
                log.completed_at = datetime.utcnow()
            else:
                log.status = "queued"
                log.scheduled_at = datetime.utcnow() + timedelta(seconds=RETRY_DELAY_SEC)
            await db.commit()
            logger.warning(
                "[CAMPAIGN SCHEDULER] Failed id=%s retry=%s/%s: %s",
                log.id,
                log.retry_count,
                log.max_retries,
                exc,
            )
            return False


campaign_scheduler = CampaignScheduler()
