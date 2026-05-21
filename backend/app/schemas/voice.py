from typing import Optional, Literal, Any, Dict
from pydantic import BaseModel


class VoiceControlMessage(BaseModel):
    """Base schema for client-initiated control messages sent over WebSocket."""
    type: Literal["start", "stop", "config", "pause", "resume"]
    payload: Optional[Dict[str, Any]] = None


class VoiceSessionStart(BaseModel):
    """Payload sent by client to start a voice streaming session."""
    patient_id: int
    input_sample_rate: int = 16000
    source_language: str = "auto"  # auto, en, es, fr, zh, etc.
    target_language: str = "en"    # language to translate into, if applicable


class VoiceServerEvent(BaseModel):
    """Schema for server-initiated events broadcasted to the client."""
    event: Literal[
        "connected", 
        "started", 
        "transcript_diff", 
        "audio_response", 
        "summary_completed", 
        "error"
    ]
    session_id: str
    payload: Dict[str, Any]
