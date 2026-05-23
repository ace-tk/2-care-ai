from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app import schemas, models
from app.api import deps

router = APIRouter()


@router.get("/", response_model=List[schemas.Transcript])
async def read_transcripts(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: models.User = Depends(deps.get_current_user),
) -> Any:
    """Retrieve all audio transcripts recorded by the clinician."""
    # Clinicians only see their own transcripts, superusers see all
    if current_user.is_superuser:
        query = select(models.Transcript).offset(skip).limit(limit)
    else:
        query = select(models.Transcript).where(
            models.Transcript.creator_id == current_user.id
        ).offset(skip).limit(limit)
        
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{transcript_id}", response_model=schemas.Transcript)
async def read_transcript(
    transcript_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> Any:
    """Retrieve details and summary of a specific consultation transcript."""
    transcript = await db.get(models.Transcript, transcript_id)
    if not transcript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript not found",
        )
        
    # Check permissions
    if transcript.creator_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this transcript.",
        )
        
    return transcript


@router.get("/patient/{patient_id}", response_model=List[schemas.Transcript])
async def read_patient_transcripts(
    patient_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> Any:
    """Retrieve consultation history for a given patient."""
    if current_user.is_superuser:
        query = select(models.Transcript).where(models.Transcript.patient_id == patient_id)
    else:
        query = select(models.Transcript).where(
            models.Transcript.patient_id == patient_id,
            models.Transcript.creator_id == current_user.id
        )
        
    result = await db.execute(query)
    return list(result.scalars().all())


@router.put("/{transcript_id}", response_model=schemas.Transcript)
async def update_transcript(
    *,
    transcript_id: int,
    db: AsyncSession = Depends(deps.get_db),
    transcript_in: schemas.TranscriptUpdate,
    current_user: models.User = Depends(deps.get_current_user),
) -> Any:
    """Manually update or correct transcript dialogue or clinical summaries."""
    transcript = await db.get(models.Transcript, transcript_id)
    if not transcript:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript not found",
        )
        
    # Check permissions
    if transcript.creator_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to edit this transcript.",
        )
        
    # Apply updates
    update_data = transcript_in.model_dump(exclude_unset=True)
    for field in update_data:
        setattr(transcript, field, update_data[field])
        
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    return transcript
