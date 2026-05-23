"""Schemas for voice HTTP chat and WebSocket events."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ── WebSocket control messages (client → server) ─────────────────────────────

class VoiceSessionStart(BaseModel):
    patient_id: int
    source_language: str = "auto"


class VoiceTextPayload(BaseModel):
    text: str


class VoiceControlMessage(BaseModel):
    type: Literal["start", "text", "stop", "trigger_outbound"]
    payload: Dict[str, Any] = Field(default_factory=dict)


# ── WebSocket server events (server → client) ──────────────────────────────────

class VoiceServerEvent(BaseModel):
    event: str
    session_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)


# ── HTTP POST /voice/chat ──────────────────────────────────────────────────────

class VoiceChatMetrics(BaseModel):
    stt_latency_ms: float = 0
    llm_latency_ms: float = 0
    tts_latency_ms: float = 0
    total_latency_ms: float = 0


class VoiceChatResponse(BaseModel):
    session_id: str
    transcript: str
    detected_language: str
    ai_response: str
    audio_base64: str = Field(description="MP3 audio encoded as base64")
    audio_mime_type: str = "audio/mpeg"
    reasoning_traces: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: VoiceChatMetrics
