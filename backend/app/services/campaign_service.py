import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.core.database import AsyncSessionLocal

from backend.app.models.appointment import Appointment

logger = logging.getLogger(__name__)

class CampaignQueueItem:
    def __init__(self, appointment_id: int, patient_id: int, target_time: datetime):
        self.appointment_id = appointment_id
        self.patient_id = patient_id
        self.target_time = target_time
        self.status = "pending" # pending, processing, completed, failed

class CampaignService:
    """Lightweight in-memory queue for outbound AI campaigns.
    Designed for future transition to Redis/Celery.
    """
    
    def __init__(self):
        self.queue: List[CampaignQueueItem] = []
        self._is_running = False
        self._worker_task = None
        
    async def start_worker(self):
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._process_queue())
        logger.info("Outbound campaign worker started.")
        
    async def stop_worker(self):
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            logger.info("Outbound campaign worker stopped.")
            
    async def queue_reminder(self, appointment_id: int, patient_id: int, target_time: datetime):
        item = CampaignQueueItem(appointment_id, patient_id, target_time)
        self.queue.append(item)
        logger.info(f"Queued outbound reminder for appointment {appointment_id}")

    async def scan_for_upcoming_appointments(self, db: AsyncSession):
        """Finds appointments in the next 24 hours that need reminders."""
        now = datetime.utcnow()
        target = now + timedelta(hours=24)
        
        # For lightweight architecture, query upcoming within the 24 hour window
        query = select(Appointment).where(
            Appointment.scheduled_start >= now,
            Appointment.scheduled_start <= target,
            Appointment.status == "scheduled"
        )
        result = await db.execute(query)
        appointments = result.scalars().all()
        
        queued_ids = [item.appointment_id for item in self.queue]
        
        for appt in appointments:
            if appt.id not in queued_ids:
                await self.queue_reminder(appt.id, appt.patient_id, appt.scheduled_start)
                
    async def prepare_outbound_call(self, item: CampaignQueueItem) -> Dict[str, Any]:
        """Prepares the LangGraph state and prompt for an outbound AI call."""
        # In a real deployment, this would interface with a telephony provider (e.g. Twilio/Vapi)
        logger.info(f"Preparing outbound call for Patient {item.patient_id}, Appt {item.appointment_id}")
        
        initial_prompt = (
            "You are calling to remind the patient of their upcoming appointment. "
            "Start the conversation warmly, confirm their identity, and ask if they plan to attend or need to reschedule."
        )
        
        return {
            "session_type": "outbound",
            "patient_id": item.patient_id,
            "appointment_id": item.appointment_id,
            "initial_system_prompt": initial_prompt
        }

    async def _process_queue(self):
        while self._is_running:
            try:
                # Scan for new upcoming appointments to queue
                try:
                    async with AsyncSessionLocal() as db:
                        await self.scan_for_upcoming_appointments(db)
                except Exception as e:
                    logger.error(f"Error scanning for upcoming appointments: {e}")

                now = datetime.utcnow()
                for item in self.queue:
                    if item.status == "pending":
                        # Trigger reminder if appointment is within 24 hours
                        time_until_appt = item.target_time - now
                        if timedelta(0) < time_until_appt <= timedelta(hours=24):
                            item.status = "processing"
                            try:
                                await self.prepare_outbound_call(item)
                                item.status = "completed"
                                logger.info(f"Successfully processed reminder for appt {item.appointment_id}")
                            except Exception as e:
                                item.status = "failed"
                                logger.error(f"Failed to process reminder for appt {item.appointment_id}: {e}")
                                
                # Remove completed/failed items to prevent memory leak
                self.queue = [i for i in self.queue if i.status in ["pending", "processing"]]
            except Exception as e:
                logger.error(f"Error in campaign worker loop: {e}")
                
            await asyncio.sleep(60) # Check queue every minute
            
campaign_service = CampaignService()
