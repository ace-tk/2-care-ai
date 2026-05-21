from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app import schemas
from backend.app.api import deps
from backend.app.services.patient_service import patient_service

router = APIRouter()


@router.get("/", response_model=List[schemas.Patient])
async def read_patients(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: Any = Depends(deps.get_current_user),
) -> Any:
    """Retrieve all patients in the system with pagination."""
    patients = await patient_service.get_multi(db, skip=skip, limit=limit)
    return patients


@router.post("/", response_model=schemas.Patient, status_code=status.HTTP_201_CREATED)
async def create_patient(
    *,
    db: AsyncSession = Depends(deps.get_db),
    patient_in: schemas.PatientCreate,
    current_user: Any = Depends(deps.get_current_user),
) -> Any:
    """Create a new patient record."""
    # Check if patient MRN already exists
    existing = await patient_service.get_by_mrn(db, mrn=patient_in.medical_record_number)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient with this Medical Record Number (MRN) already exists.",
        )
    patient = await patient_service.create(db, obj_in=patient_in.model_dump())
    return patient


@router.get("/{patient_id}", response_model=schemas.Patient)
async def read_patient(
    patient_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: Any = Depends(deps.get_current_user),
) -> Any:
    """Retrieve details of a specific patient."""
    patient = await patient_service.get(db, id=patient_id)
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found",
        )
    return patient


@router.put("/{patient_id}", response_model=schemas.Patient)
async def update_patient(
    *,
    patient_id: int,
    db: AsyncSession = Depends(deps.get_db),
    patient_in: schemas.PatientUpdate,
    current_user: Any = Depends(deps.get_current_user),
) -> Any:
    """Update patient information."""
    patient = await patient_service.get(db, id=patient_id)
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found",
        )
    patient = await patient_service.update(db, db_obj=patient, obj_in=patient_in)
    return patient


@router.delete("/{patient_id}", response_model=schemas.Patient)
async def delete_patient(
    *,
    patient_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: Any = Depends(deps.get_current_user),
) -> Any:
    """Remove patient record."""
    patient = await patient_service.get(db, id=patient_id)
    if not patient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found",
        )
    await patient_service.remove(db, id=patient_id)
    return patient
