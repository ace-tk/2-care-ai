"""
Multilingual memory and language detection tests.

Run from backend/:
  python -m pytest tests/test_multilingual_memory.py -v
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import AsyncSessionLocal, Base, engine
from app.db.seed import run_startup_seed
from app.models.patient import Patient
from app.services import session_memory
from app.services.language_service import (
    detect_from_text,
    merge_detections,
    normalize_language_code,
)
from app.services.memory_manager import (
    bind_patient_to_session,
    build_prompt_context,
    clear_session_cache,
    resolve_language,
)
from app.services.patient_memory_service import (
    load_patient_memory,
    update_language_preference,
)


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module", autouse=True)
async def setup_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.db.migrations import apply_dev_schema_patches

        await apply_dev_schema_patches(conn)
    async with AsyncSessionLocal() as db:
        await run_startup_seed(db)
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_script_detection_hindi():
    result = detect_from_text("मुझे कल दंत चिकित्सक का अपॉइंटमेंट चाहिए")
    assert result.code == "hi"
    assert result.confidence >= 0.5


@pytest.mark.asyncio
async def test_script_detection_tamil():
    result = detect_from_text("நாளை மருத்துவர் சந்திப்பு வேண்டும்")
    assert result.code == "ta"
    assert result.confidence >= 0.5


@pytest.mark.asyncio
async def test_low_confidence_defaults_to_english():
    result = merge_detections(text="ok", groq_language="other", groq_confidence=0.2)
    assert result.code == "en"
    assert result.source in ("low_confidence_default", "latin_script", "script_fallback")


@pytest.mark.asyncio
async def test_session_slot_context_retained():
    session_id = "test-session-slot"
    session_memory.update_session(
        session_id,
        selected_doctor_name="Dr. Sarah Jenkins",
        selected_slot="2026-05-24 17:00",
        workflow_state="slots_offered",
        offered_slots=["2026-05-24 17:00", "2026-05-24 18:00"],
    )
    block, meta = build_prompt_context(session_id)
    assert "Dr. Sarah Jenkins" in block
    assert "17:00" in block
    assert meta["session_state"]["workflow_state"] == "slots_offered"
    session_memory.clear_session(session_id)


@pytest.mark.asyncio
async def test_patient_language_persisted_and_loaded():
    async with AsyncSessionLocal() as db:
        patient = await db.get(Patient, 1)
        assert patient is not None
        await update_language_preference(db, 1, "hi")
        snapshot = await load_patient_memory(db, 1)
        assert snapshot is not None
        assert snapshot.language_preference == "hi"


@pytest.mark.asyncio
async def test_cross_session_memory_via_bind():
    session_a = "session-a"
    session_b = "session-b"
    clear_session_cache(session_a)
    clear_session_cache(session_b)

    await bind_patient_to_session(session_a, 1)
    snap_a = await resolve_language(session_a, "Book dentist tomorrow")
    assert snap_a.code in ("en", "hi", "ta")

    await bind_patient_to_session(session_b, 1)
    lang_b = await resolve_language(session_b, "hello")
    sess = session_memory.get_session(session_b)
    assert sess.language_code == "hi" or lang_b.code == "hi"

    clear_session_cache(session_a)
    clear_session_cache(session_b)
    session_memory.clear_session(session_a)
    session_memory.clear_session(session_b)


@pytest.mark.asyncio
async def test_english_booking_intent_session_update():
    session_id = "test-en-booking"
    session_memory.update_session(session_id, last_intent="booking", requested_specialty="Dentist")
    ctx = session_memory.get_session(session_id)
    assert ctx.last_intent == "booking"
    assert ctx.requested_specialty == "Dentist"
    session_memory.clear_session(session_id)


@pytest.mark.asyncio
async def test_hindi_reschedule_context_in_prompt():
    session_id = "test-hi-reschedule"
    session_memory.update_session(
        session_id,
        language_code="hi",
        last_intent="rescheduling",
        active_appointment_id=42,
        appointment_time="2026-05-25 17:00",
    )
    block, _ = build_prompt_context(session_id)
    assert "rescheduling" in block or "APT-42" in block or "42" in block
    session_memory.clear_session(session_id)


@pytest.mark.asyncio
async def test_tamil_cancellation_language():
    detection = merge_detections(
        text="என் அப்பாயிண்ட்மெண்ட்டை ரத்து செய்ய வேண்டும்",
        groq_language="tamil",
        groq_confidence=0.9,
    )
    assert detection.code == "ta"


def test_normalize_language_codes():
    assert normalize_language_code("hindi") == "hi"
    assert normalize_language_code("tamil") == "ta"
    assert normalize_language_code("en-US") == "en"
