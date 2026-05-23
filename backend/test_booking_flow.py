"""
Integration test for dentist booking flow (no Groq API required).

Run from backend/:
  .venv/bin/python test_booking_flow.py
"""

import os

# Must be set before any app imports load Settings / engine
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./twocare.db")

import asyncio
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
if "app.services" not in sys.modules:
    _services_pkg = types.ModuleType("app.services")
    _services_pkg.__path__ = [str(_BACKEND / "app" / "services")]
    sys.modules["app.services"] = _services_pkg

from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.db.seed import run_startup_seed
from app.services.doctor_service import search_doctors_by_specialty
from app.services.scheduling_service import book_appointment, find_nearest_available_slot
from app.services.specialty import extract_specialty_from_text


async def main() -> None:
    print(f"Using database: {settings.DATABASE_URL} -> {settings.async_database_url}")
    assert settings.uses_sqlite, "This test expects SQLite (set DATABASE_URL=sqlite:///./twocare.db)"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.db.migrations import apply_dev_schema_patches

        await apply_dev_schema_patches(conn)

    async with AsyncSessionLocal() as db:
        await run_startup_seed(db)

        user_text = "Book dentist appointment tomorrow at 5 PM"
        specialty = extract_specialty_from_text(user_text)
        assert specialty == "Dentist", f"Expected Dentist, got {specialty}"
        print(f"✓ Specialty resolved: {specialty}")

        search = await search_doctors_by_specialty(db, "dentist")
        assert search["success"] and search["count"] >= 1, search
        doctor_id = search["doctors"][0]["id"]
        print(f"✓ Found {search['count']} dentist(s): {search['doctors'][0]['name']}")

        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        slot_time = f"{tomorrow} 17:00"

        nearest = await find_nearest_available_slot(db, doctor_id, slot_time)
        assert nearest["success"], nearest
        print(f"✓ Nearest slot: {nearest['slot_time']}")

        result = await book_appointment(
            db,
            doctor_id=doctor_id,
            specialty=specialty,
            slot_time=slot_time,
            reason="Dental checkup",
        )
        assert result["success"], result
        assert result["specialty"] == "Dentist"
        assert result["booking_id"].startswith("APT-")
        print("✓ Booking saved:")
        print(f"  {result}")


if __name__ == "__main__":
    asyncio.run(main())
