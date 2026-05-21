from datetime import date
from typing import Optional
from pydantic import BaseModel


class PatientBase(BaseModel):
    medical_record_number: str
    first_name: str
    last_name: str
    date_of_birth: date
    language_preference: str = "en"
    is_active: bool = True


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    language_preference: Optional[str] = None
    is_active: Optional[bool] = None


class Patient(PatientBase):
    id: int

    class Config:
        from_attributes = True
