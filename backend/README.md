# 2Care AI - Backend API

Production-style modular FastAPI backend for a realtime multilingual healthcare voice AI system.

## Directory Structure

```text
backend/
├── app/
│   ├── api/                 # API controllers
│   │   ├── deps.py          # Injection dependencies (auth, db)
│   │   └── v1/              # Version 1 Router
│   │       ├── endpoints/
│   │       │   ├── auth.py        # Clinician registration & JWT login
│   │       │   ├── patients.py    # Patients CRUD operations
│   │       │   ├── transcripts.py # Dialogue histories & SOAP summaries
│   │       │   └── voice.py       # Realtime WebSocket Audio Server
│   │       └── router.py      # Combines endpoints
│   ├── core/                # Core configurations
│   │   ├── config.py        # Environment validation via Pydantic
│   │   ├── database.py      # Async SQLAlchemy (SQLite local / PostgreSQL prod)
│   │   ├── logging.py       # Centralized colored logging configurations
│   │   └── security.py      # Password encryption and JWT tokens
│   ├── models/              # SQLAlchemy 2.0 Database Models
│   │   ├── patient.py       # Patient schema model
│   │   ├── appointment.py   # Appointments schema model
│   │   ├── transcript.py    # Transcripts & Summaries schema model
│   │   └── user.py          # Clinician schema model
│   ├── schemas/             # Pydantic schemas (Request / Response validation)
│   ├── services/            # Service Layer (Business logic orchestrators)
│   │   ├── appointment_service.py # Core scheduling business logic
│   │   ├── base.py          # Generic DB CRUD base service
│   │   ├── campaign_service.py    # Outbound background reminder queue
│   │   ├── llm_service.py   # OpenAI / Reasoning / LangGraph entrypoint
│   │   ├── patient_service.py # Database operations for patients
│   │   ├── stt_service.py   # Deepgram Speech-to-Text streaming logic
│   │   ├── tts_service.py   # ElevenLabs Text-to-Speech playback logic
│   │   └── voice_service.py # WebSocket & Audio Session orchestrator
│   ├── tools/               # Agentic Tools
│   │   └── appointment_tools.py # Tools for LangGraph to execute 
│   ├── workflows/           # LangGraph StateGraphs
│   │   └── orchestrator.py  # Conversational multi-agent routing graph
│   └── main.py              # Application entry point & Lifespan managers
├── .env.example             # Configuration variables blueprint
├── Dockerfile               # Multi-stage production container
└── requirements.txt         # Package dependencies
```

## Getting Started

### Prerequisites

- Python 3.10+
- **Local dev:** SQLite only (`twocare.db` is created automatically — no PostgreSQL install needed)
- **Production / Docker:** PostgreSQL (see `docker-compose.yml`)

### Installation & Local Run

1. **Create Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Settings:**
   ```bash
   cp .env.example .env
   # Default DATABASE_URL=sqlite:///./twocare.db (no Postgres required)
   # For production, set DATABASE_URL=postgresql+asyncpg://...
   ```

4. **Launch Server:**
   ```bash
   uvicorn app.main:app --reload
   ```
   The API will be available at `http://localhost:8000`. Swagger documentation can be viewed at `http://localhost:8000/docs`.

## Realtime Voice WebSocket API

- **Streaming Endpoint:** `ws://localhost:8000/api/v1/voice/stream?token=<JWT_TOKEN>`

### Client Control Messages

Clients can configure and stop streaming sessions using JSON messages:

```json
// To start the session
{
  "type": "start",
  "payload": {
    "patient_id": 1,
    "source_language": "es",
    "target_language": "en"
  }
}

// To finalize the session and compile SOAP notes
{
  "type": "stop"
}
```

### Server Events

The server sends messages back to the client detailing real-time events:

```json
// Realtime transcription update
{
  "event": "transcript_diff",
  "session_id": "session-uuid",
  "payload": {
    "original_text": "Hola doctor",
    "translated_text": "Hello doctor",
    "language": "es",
    "is_final": false
  }
}

// Summary generation finalized
{
  "event": "summary_completed",
  "session_id": "session-uuid",
  "payload": {
    "transcript_id": 12,
    "clinical_summary": "SUBJECTIVE:\nPatient presented... CLINICAL PLAN:..."
  }
}
```
