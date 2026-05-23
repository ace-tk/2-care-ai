"""Verify Groq message sanitization and SQLite cancellation."""

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./twocare.db")

_BACKEND = Path(__file__).resolve().parent
if "app.services" not in sys.modules:
    pkg = types.ModuleType("app.services")
    pkg.__path__ = [str(_BACKEND / "app" / "services")]
    sys.modules["app.services"] = pkg

from app.services.groq_service import _build_groq_messages, _sanitize_message_for_groq


def test_strips_annotations():
    dirty = {
        "role": "assistant",
        "content": None,
        "annotations": [],
        "refusal": None,
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "cancel_appointment", "arguments": '{"appointment_id": 1}'},
                "annotations": [],
            }
        ],
    }
    clean = _sanitize_message_for_groq(dirty)
    assert "annotations" not in clean
    assert clean["role"] == "assistant"
    assert clean["tool_calls"][0]["function"]["name"] == "cancel_appointment"
    assert "annotations" not in clean["tool_calls"][0]
    print("✓ annotations stripped from assistant + tool_calls")


def test_build_groq_messages():
    history = [
        {"role": "user", "content": "Cancel my appointment"},
        {
            "role": "assistant",
            "content": None,
            "annotations": [],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "cancel_appointment",
                        "arguments": '{"appointment_id": 1}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "cancel_appointment",
            "content": '{"success": true}',
            "annotations": [],
        },
    ]
    msgs = _build_groq_messages("You are helpful.", history)
    assert msgs[0] == {"role": "system", "content": "You are helpful."}
    for m in msgs:
        assert "annotations" not in m
        for key in m:
            assert key in ("role", "content", "tool_calls", "tool_call_id", "name")
    print("✓ _build_groq_messages produces Groq-safe payload")


async def test_sqlite_cancel():
    import asyncio
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal, Base, engine
    from app.db.seed import run_startup_seed
    from app.models.appointment import Appointment
    from app.services.scheduling_service import book_appointment, cancel_appointment

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        await run_startup_seed(db)
        book = await book_appointment(
            db,
            doctor_id=1,
            specialty="Dentist",
            slot_time="2026-06-01 17:00",
            reason="Test",
        )
        assert book["success"], book
        appt_id = book["appointment"]["id"]

        result = await cancel_appointment(db, appt_id)
        assert result["success"], result

        row = await db.get(Appointment, appt_id)
        assert row.status == "cancelled"
        print(f"✓ SQLite cancel: APT-{appt_id} status={row.status}")


if __name__ == "__main__":
    test_strips_annotations()
    test_build_groq_messages()
    import asyncio

    asyncio.run(test_sqlite_cancel())
