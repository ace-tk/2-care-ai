from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TranscriptBase(BaseModel):
    patient_id: int
    detected_language: str = "en"
    original_text: Optional[str] = None
    translated_text: Optional[str] = None
    clinical_summary: Optional[str] = None


class TranscriptCreate(BaseModel):
    patient_id: int
    session_id: str
    detected_language: Optional[str] = "en"


class TranscriptUpdate(BaseModel):
    audio_url: Optional[str] = None
    detected_language: Optional[str] = None
    original_text: Optional[str] = None
    translated_text: Optional[str] = None
    clinical_summary: Optional[str] = None


class Transcript(TranscriptBase):
    id: int
    creator_id: int
    session_id: str
    audio_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
