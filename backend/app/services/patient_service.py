from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from backend.app.models.patient import Patient
from backend.app.services.base import BaseService


class PatientService(BaseService[Patient]):
    """Service layer for patient-specific database operations."""

    def __init__(self):
        super().__init__(Patient)

    async def get_by_mrn(self, db: AsyncSession, mrn: str) -> Optional[Patient]:
        """Retrieve a patient by their Medical Record Number (MRN)."""
        query = select(self.model).where(self.model.medical_record_number == mrn)
        result = await db.execute(query)
        return result.scalars().first()


patient_service = PatientService()
