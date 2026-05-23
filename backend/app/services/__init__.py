# Lazy exports — avoid importing voice_service (and heavy SDK deps) at package load.
from app.services.patient_service import patient_service
from app.services.campaign_service import campaign_service

__all__ = ["patient_service", "campaign_service"]
