from fastapi import APIRouter
from backend.app.api.v1.endpoints import auth, patients, transcripts, voice

api_router = APIRouter()

# API version 1 routing
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(patients.router, prefix="/patients", tags=["patients"])
api_router.include_router(transcripts.router, prefix="/transcripts", tags=["transcripts"])
api_router.include_router(voice.router, prefix="/voice", tags=["voice"])
