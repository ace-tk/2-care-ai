"""
Outbound campaign orchestration: reminders, follow-ups, reschedule, recovery.

Integrates with WebSocket voice sessions, multilingual memory, scheduling, and DB logs.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.campaign_log import CampaignLog
from app.models.patient import Patient
from app.services import session_memory
from app.services.doctor_service import get_doctor
from app.services.language_service import detect_intent_from_text, normalize_language_code
from app.services.memory_manager import bind_patient_to_session
from app.services.patient_memory_service import load_patient_memory

logger = logging.getLogger(__name__)

TraceCallback = Optional[Callable[[dict], Awaitable[None]]]

CAMPAIGN_TYPES = frozenset({
    "reminder",
    "followup",
    "reschedule",
    "cancellation_recovery",
})

_CAMPAIGN_ALIASES = {
    "appointment reminder": "reminder",
    "appointment_reminder": "reminder",
    "post-op follow-up": "followup",
    "post_op_followup": "followup",
    "post-op followup": "followup",
    "follow-up": "followup",
    "follow_up": "followup",
    "cancellation recovery": "cancellation_recovery",
    "missed appointment": "cancellation_recovery",
    "missed_appointment": "cancellation_recovery",
}

_DEFAULT_TEMPLATES = {
    "reminder": {
        "en": "Hello {name}, this is 2Care AI reminding you of your appointment{appt_detail}. Will you attend, or would you like to reschedule?",
        "hi": "नमस्ते {name}, यह 2Care AI है। आपकी अपॉइंटमेंट{appt_detail} की याद दिलाना चाहते हैं। क्या आप आएंगे या समय बदलना है?",
        "ta": "வணக்கம் {name}, இது 2Care AI. உங்கள் சந்திப்பு{appt_detail} பற்றி நினைவூட்டுகிறோம். வருவீர்களா அல்லது நேரம் மாற்ற வேண்டுமா?",
    },
    "followup": {
        "en": "Hello {name}, this is 2Care AI following up after your recent visit. How are you feeling? Do you have any concerns we should address?",
        "hi": "नमस्ते {name}, 2Care AI आपकी हाल की विज़िट के बाद संपर्क कर रहा है। आप कैसा महसूस कर रहे हैं? कोई चिंता है?",
        "ta": "வணக்கம் {name}, 2Care AI உங்கள் சமீபத்திய வருகைக்குப் பிறகு தொடர்பு கொள்கிறது. எப்படி இருக்கிறீர்கள்? ஏதேனும் கவலை உள்ளதா?",
    },
    "reschedule": {
        "en": "Hello {name}, we can help reschedule your appointment{appt_detail}. What day and time works best for you?",
        "hi": "नमस्ते {name}, हम आपकी अपॉइंटमेंट{appt_detail} का समय बदलने में मदद कर सकते हैं। कौन सा दिन और समय ठीक रहेगा?",
        "ta": "வணக்கம் {name}, உங்கள் சந்திப்பு{appt_detail} நேரத்தை மாற்ற உதவலாம். எந்த நாள்/நேரம் வசதி?",
    },
    "cancellation_recovery": {
        "en": "Hello {name}, we noticed you missed your appointment{appt_detail}. We're sorry for the inconvenience — would you like to rebook?",
        "hi": "नमस्ते {name}, आप अपॉइंटमेंट{appt_detail} पर नहीं आए। क्षमा करें — क्या आप नया समय बुक करना चाहेंगे?",
        "ta": "வணக்கம் {name}, சந்திப்பு{appt_detail} தவறவிட்டீர்கள். மன்னிக்கவும் — புதிய நேரம் பதிவு செய்ய விரும்புகிறீர்களா?",
    },
}

_OPENERS = {
    "reminder": {
        "en": (
            "SYSTEM OUTBOUND REMINDER: You are calling the patient about an upcoming appointment. "
            "Greet warmly, state the appointment details, and ask if they will attend or need to reschedule."
        ),
        "hi": (
            "SYSTEM OUTBOUND REMINDER (हिंदी): रोगी को आगामी अपॉइंटमेंट की याद दिलाएं। "
            "विनम्र अभिवादन करें, समय बताएं, और पूछें कि वे आएंगे या समय बदलना है। केवल हिंदी में जवाब दें।"
        ),
        "ta": (
            "SYSTEM OUTBOUND REMINDER (தமிழ்): வரவிருக்கும் சந்திப்பை நினைவூட்டுங்கள். "
            "வாழ்த்து சொல்லி நேரம் சொல்லி வருவார்களா அல்லது மாற்ற வேண்டுமா என்று கேளுங்கள். தமிழில் மட்டும் பதிலளிக்கவும்."
        ),
    },
    "followup": {
        "en": (
            "SYSTEM OUTBOUND FOLLOW-UP: Post-operative check-in. Ask how they are feeling, "
            "whether they have concerns, and if they need a follow-up visit."
        ),
        "hi": (
            "SYSTEM OUTBOUND FOLLOW-UP (हिंदी): ऑपरेशन के बाद की जांच। "
            "उनका स्वास्थ्य पूछें और क्या कोई समस्या है। केवल हिंदी में जवाब दें।"
        ),
        "ta": (
            "SYSTEM OUTBOUND FOLLOW-UP (தமிழ்): அறுவை சிகிச்சைக்குப் பிறகு பின்தொடர்தல். "
            "உடல்நிலை கேட்டு கவலை உள்ளதா என்று கேளுங்கள். தமிழில் மட்டும் பதிலளிக்கவும்."
        ),
    },
    "reschedule": {
        "en": (
            "SYSTEM OUTBOUND RESCHEDULE: The patient may need a new appointment time. "
            "Offer to find available slots and reschedule using scheduling tools."
        ),
        "hi": (
            "SYSTEM OUTBOUND RESCHEDULE (हिंदी): रोगी को नया समय चाहिए हो सकता है। "
            "उपलब्ध स्लॉट खोजकर पुनर्निर्धारण करने में मदद करें। केवल हिंदी में जवाब दें।"
        ),
        "ta": (
            "SYSTEM OUTBOUND RESCHEDULE (தமிழ்): புதிய நேரம் தேவைப்படலாம். "
            "கிடைக்கும் நேரங்களைக் கண்டுபிடித்து மாற்றுங்கள். தமிழில் மட்டும் பதிலளிக்கவும்."
        ),
    },
    "cancellation_recovery": {
        "en": (
            "SYSTEM OUTBOUND RECOVERY: Patient missed an appointment. "
            "Apologize for the inconvenience and offer to rebook at a convenient time."
        ),
        "hi": (
            "SYSTEM OUTBOUND RECOVERY (हिंदी): रोगी अपॉइंटमेंट पर नहीं आए। "
            "क्षमा करें और नया समय बुक करने की पेशकश करें। केवल हिंदी में जवाब दें।"
        ),
        "ta": (
            "SYSTEM OUTBOUND RECOVERY (தமிழ்): நோயாளி சந்திப்பைத் தவறவிட்டார். "
            "மன்னித்து புதிய நேரம் பதிவு செய்ய உதவுங்கள். தமிழில் மட்டும் பதிலளிக்கவும்."
        ),
    },
}

_RESPONSE_PATTERNS = {
    "confirmed": re.compile(
        r"\b(confirm|yes|attend|coming|ok|okay|haan|ha|theek|சரி|ஆம்|உறுதி)\b",
        re.I,
    ),
    "reschedule": re.compile(
        r"\b(reschedule|change|badlo|badal|shift|மாற்ற|புதிய நேர)\b",
        re.I,
    ),
    "cancel": re.compile(
        r"\b(cancel|cancelled|radd|रद्द|ரத்து|don't want|नहीं)\b",
        re.I,
    ),
    "reject_politely": re.compile(
        r"\b(no thanks|not interested|decline|don't call|nahi chahiye|வேண்டாம்|இல்லை நன்றி)\b",
        re.I,
    ),
    "callback_later": re.compile(
        r"\b(later|call back|busy|abhi nahi|பிறகு|பிறகு அழை)\b",
        re.I,
    ),
}


def normalize_campaign_type(raw: str) -> str:
    key = (raw or "reminder").strip().lower().replace("-", "_")
    if key in CAMPAIGN_TYPES:
        return key
    return _CAMPAIGN_ALIASES.get(key, _CAMPAIGN_ALIASES.get(raw.strip().lower(), "reminder"))


class CampaignService:
    """Schedule, queue, and run outbound AI campaigns."""

    async def start_worker(self) -> None:
        from app.services.campaign_scheduler import campaign_scheduler

        await campaign_scheduler.start()

    async def stop_worker(self) -> None:
        from app.services.campaign_scheduler import campaign_scheduler

        await campaign_scheduler.stop()

    async def schedule_campaign(
        self,
        db: AsyncSession,
        *,
        patient_id: int,
        campaign_type: str,
        appointment_id: Optional[int] = None,
        scheduled_time: Optional[datetime] = None,
        preferred_language: Optional[str] = None,
        message_template: Optional[str] = None,
    ) -> CampaignLog:
        ctype = normalize_campaign_type(campaign_type)
        patient = await db.get(Patient, patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        lang = normalize_language_code(
            preferred_language or patient.language_preference or "en"
        )
        run_at = scheduled_time or datetime.utcnow()

        log = CampaignLog(
            patient_id=patient_id,
            appointment_id=appointment_id,
            campaign_type=ctype,
            status="queued",
            scheduled_at=run_at,
            preferred_language=lang,
            message_template=message_template,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)

        logger.info(
            "[CAMPAIGN SCHEDULED] id=%s | type=%s | patient=%s | lang=%s | at=%s",
            log.id,
            ctype,
            patient_id,
            lang,
            run_at.isoformat(),
        )
        return log

    async def _resolve_appointment_context(
        self,
        db: AsyncSession,
        patient_id: int,
        appointment_id: Optional[int],
    ) -> tuple[Optional[int], str]:
        appt_detail = ""
        if appointment_id:
            appt = await db.get(Appointment, appointment_id)
            if appt:
                doctor = await get_doctor(db, appt.doctor_id)
                appt_detail = (
                    f" with {doctor.full_name if doctor else 'your doctor'} "
                    f"on {appt.scheduled_start.strftime('%Y-%m-%d at %H:%M')}"
                )
                return appointment_id, appt_detail

        result = await db.execute(
            select(Appointment)
            .where(
                Appointment.patient_id == patient_id,
                Appointment.status == "scheduled",
                Appointment.scheduled_start >= datetime.utcnow(),
            )
            .order_by(Appointment.scheduled_start)
            .limit(1)
        )
        appt = result.scalar_one_or_none()
        if appt:
            doctor = await get_doctor(db, appt.doctor_id)
            appt_detail = (
                f" with {doctor.full_name if doctor else 'your doctor'} "
                f"on {appt.scheduled_start.strftime('%Y-%m-%d at %H:%M')}"
            )
            return appt.id, appt_detail
        return None, ""

    async def generate_outbound_message(
        self,
        db: AsyncSession,
        *,
        patient_id: int,
        campaign_type: str,
        appointment_id: Optional[int] = None,
        preferred_language: str = "en",
        message_template: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build multilingual outbound message using patient memory + templates."""
        ctype = normalize_campaign_type(campaign_type)
        patient = await db.get(Patient, patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        lang = normalize_language_code(
            preferred_language or patient.language_preference or "en"
        )
        name = f"{patient.first_name} {patient.last_name}".strip()
        _, appt_detail = await self._resolve_appointment_context(
            db, patient_id, appointment_id
        )

        memory = await load_patient_memory(db, patient_id)
        memory_hint = ""
        if memory and memory.last_interaction_summary:
            memory_hint = f" Context: {memory.last_interaction_summary[:200]}."

        if message_template:
            message = message_template.format(
                name=name, appt_detail=appt_detail
            )
        else:
            tpl = _DEFAULT_TEMPLATES.get(ctype, _DEFAULT_TEMPLATES["reminder"])
            message = tpl.get(lang, tpl["en"]).format(name=name, appt_detail=appt_detail)

        if memory_hint:
            message = f"{message}{memory_hint}"

        return {"message": message, "language": lang, "campaign_type": ctype}

    async def build_outbound_turn(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        patient_id: int,
        campaign_type: str,
        appointment_id: Optional[int] = None,
        preferred_language: Optional[str] = None,
        message_template: Optional[str] = None,
        campaign_log_id: Optional[int] = None,
        trace_callback: TraceCallback = None,
    ) -> Dict[str, Any]:
        """Prepare outbound opening instruction + DB log for a live WS session."""
        ctype = normalize_campaign_type(campaign_type)
        patient = await db.get(Patient, patient_id)
        if not patient:
            raise ValueError(f"Patient {patient_id} not found")

        lang = normalize_language_code(
            preferred_language or patient.language_preference or "en"
        )
        appointment_id, appt_context = await self._resolve_appointment_context(
            db, patient_id, appointment_id
        )

        outbound = await self.generate_outbound_message(
            db,
            patient_id=patient_id,
            campaign_type=ctype,
            appointment_id=appointment_id,
            preferred_language=lang,
            message_template=message_template,
        )

        if campaign_log_id:
            log = await db.get(CampaignLog, campaign_log_id)
            if log:
                log.session_id = session_id
                log.status = "active"
                log.started_at = datetime.utcnow()
                log.outbound_message = outbound["message"]
                log.preferred_language = lang
                await db.commit()
                await db.refresh(log)
        else:
            log = CampaignLog(
                patient_id=patient_id,
                appointment_id=appointment_id,
                session_id=session_id,
                campaign_type=ctype,
                status="active",
                preferred_language=lang,
                message_template=message_template,
                outbound_message=outbound["message"],
                started_at=datetime.utcnow(),
                scheduled_at=datetime.utcnow(),
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)

        await bind_patient_to_session(session_id, patient_id)
        session_memory.update_session(
            session_id,
            patient_id=patient_id,
            workflow_state="outbound_campaign",
            last_intent="general",
        )
        session_memory.update_session(
            session_id,
            active_campaign_type=ctype,
            active_campaign_log_id=log.id,
        )

        opener = _OPENERS.get(ctype, _OPENERS["reminder"]).get(lang, _OPENERS[ctype]["en"])
        instruction = (
            f"{opener}{appt_context} Patient: {patient.first_name} {patient.last_name}. "
            f"Deliver this outbound message naturally: {outbound['message']}"
        )

        traces = [
            {"node": "campaign_scheduled", "campaign_type": ctype, "log_id": log.id},
            {"node": "campaign_start", "campaign_type": ctype, "log_id": log.id},
            {"node": "campaign_type", "campaign_type": ctype, "language": lang},
        ]
        for t in traces:
            logger.info("[CAMPAIGN START] %s | session=%s | %s", ctype, session_id, t)
            if trace_callback:
                label = {
                    "campaign_scheduled": "[CAMPAIGN SCHEDULED]",
                    "campaign_start": "[CAMPAIGN START]",
                    "campaign_type": "[CAMPAIGN TYPE]",
                }.get(t["node"], "[CAMPAIGN]")
                await trace_callback(
                    {
                        "node": t["node"],
                        "timestamp": time.time(),
                        "tool_result": f"{label} {ctype} | lang={lang}",
                        "campaign_type": ctype,
                    }
                )
                if t["node"] == "campaign_start":
                    await trace_callback(
                        {
                            "node": "outbound_triggered",
                            "timestamp": time.time(),
                            "tool_result": f"[OUTBOUND TRIGGERED] {ctype}",
                            "campaign_type": ctype,
                        }
                    )

        return {
            "log_id": log.id,
            "instruction": instruction,
            "campaign_type": ctype,
            "language": lang,
            "patient_id": patient_id,
            "outbound_message": outbound["message"],
        }

    def classify_patient_response(self, text: str) -> str:
        if not text:
            return "unknown"
        if _RESPONSE_PATTERNS["callback_later"].search(text):
            return "callback_later"
        if _RESPONSE_PATTERNS["reject_politely"].search(text):
            return "reject_politely"
        if _RESPONSE_PATTERNS["cancel"].search(text) or detect_intent_from_text(text) == "cancellation":
            return "cancel_requested"
        if _RESPONSE_PATTERNS["reschedule"].search(text) or detect_intent_from_text(text) == "rescheduling":
            return "reschedule_requested"
        if _RESPONSE_PATTERNS["confirmed"].search(text):
            return "confirmed"
        return "general_reply"

    async def record_patient_response(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        user_text: str,
        trace_callback: TraceCallback = None,
    ) -> Optional[str]:
        sess = session_memory.get_session(session_id)
        log_id = sess.active_campaign_log_id
        if not log_id:
            return None

        result = self.classify_patient_response(user_text)
        log = await db.get(CampaignLog, log_id)
        if log:
            log.patient_response = user_text[:2000]
            log.result = result
            if result in (
                "confirmed",
                "cancel_requested",
                "reschedule_requested",
                "callback_later",
                "reject_politely",
            ):
                log.status = "completed"
                log.completed_at = datetime.utcnow()
                log.trace_summary = (
                    (log.trace_summary or "")
                    + f" | [APPOINTMENT UPDATED] patient={result}"
                )
            await db.commit()

        logger.info(
            "[CAMPAIGN] PATIENT RESPONSE | session=%s | result=%s | text='%s'",
            session_id,
            result,
            user_text[:80],
        )
        if trace_callback:
            await trace_callback(
                {
                    "node": "campaign_patient_response",
                    "timestamp": time.time(),
                    "tool_result": f"[PATIENT RESPONDED] {result}: {user_text[:120]}",
                    "campaign_result": result,
                }
            )
            await trace_callback(
                {
                    "node": "campaign_result",
                    "timestamp": time.time(),
                    "tool_result": f"[CAMPAIGN RESULT] {result}",
                    "campaign_result": result,
                }
            )
            if result in ("confirmed", "reschedule_requested", "cancel_requested"):
                await trace_callback(
                    {
                        "node": "appointment_updated",
                        "timestamp": time.time(),
                        "tool_result": f"[APPOINTMENT UPDATED] via campaign response: {result}",
                        "campaign_result": result,
                    }
                )
        return result

    async def scan_and_queue_reminders(self, db: AsyncSession) -> int:
        """Queue reminder campaigns for appointments in the next 24 hours."""
        now = datetime.utcnow()
        window = now + timedelta(hours=24)
        result = await db.execute(
            select(Appointment).where(
                Appointment.scheduled_start >= now,
                Appointment.scheduled_start <= window,
                Appointment.status == "scheduled",
            )
        )
        count = 0
        for appt in result.scalars().all():
            existing = await db.execute(
                select(CampaignLog).where(
                    CampaignLog.appointment_id == appt.id,
                    CampaignLog.campaign_type == "reminder",
                    CampaignLog.status.in_(
                        ("queued", "processing", "active", "dispatched", "completed")
                    ),
                )
            )
            if existing.scalar_one_or_none():
                continue
            patient = await db.get(Patient, appt.patient_id)
            lang = normalize_language_code(
                patient.language_preference if patient else "en"
            )
            await self.schedule_campaign(
                db,
                patient_id=appt.patient_id,
                campaign_type="reminder",
                appointment_id=appt.id,
                scheduled_time=appt.scheduled_start - timedelta(hours=2),
                preferred_language=lang,
            )
            count += 1
        return count

    async def list_campaigns(
        self,
        db: AsyncSession,
        *,
        patient_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[CampaignLog]:
        q = select(CampaignLog).order_by(CampaignLog.created_at.desc()).limit(limit)
        if patient_id is not None:
            q = q.where(CampaignLog.patient_id == patient_id)
        result = await db.execute(q)
        return list(result.scalars().all())


campaign_service = CampaignService()
