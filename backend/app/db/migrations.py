"""Lightweight dev schema patches (use Alembic in production)."""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import settings

logger = logging.getLogger(__name__)


async def apply_dev_schema_patches(conn: AsyncConnection) -> None:
    """
    Add scheduling columns to existing databases without dropping data.
    PostgreSQL uses JSONB; SQLite uses JSON/TEXT (handled by SQLAlchemy JSON type).
    """
    if settings.uses_sqlite:
        await _apply_sqlite_patches(conn)
    else:
        await _apply_postgres_patches(conn)


async def _apply_postgres_patches(conn: AsyncConnection) -> None:
    patches = [
        """
        ALTER TABLE doctors
        ADD COLUMN IF NOT EXISTS available_slots JSONB NOT NULL DEFAULT '[]'::jsonb
        """,
        """
        ALTER TABLE doctors
        ADD COLUMN IF NOT EXISTS languages JSONB NOT NULL DEFAULT '[]'::jsonb
        """,
        """
        ALTER TABLE patients
        ADD COLUMN IF NOT EXISTS preferred_doctor_id INTEGER
        REFERENCES doctors(id)
        """,
        """
        ALTER TABLE patients
        ADD COLUMN IF NOT EXISTS last_interaction_summary TEXT
        """,
    ]
    for stmt in patches:
        await conn.execute(text(stmt))
    await _apply_campaign_log_patches(conn)
    logger.info("[Schema] PostgreSQL scheduling + patient memory columns verified")


async def _apply_sqlite_patches(conn: AsyncConnection) -> None:
    def _column_names(sync_conn) -> set[str]:
        inspector = inspect(sync_conn)
        if not inspector.has_table("doctors"):
            return set()
        return {col["name"] for col in inspector.get_columns("doctors")}

    existing = await conn.run_sync(_column_names)

    if "available_slots" not in existing:
        await conn.execute(
            text(
                "ALTER TABLE doctors ADD COLUMN available_slots JSON NOT NULL DEFAULT '[]'"
            )
        )
    if "languages" not in existing:
        await conn.execute(
            text("ALTER TABLE doctors ADD COLUMN languages JSON NOT NULL DEFAULT '[]'")
        )

    patient_cols = await conn.run_sync(_patient_column_names)
    if "preferred_doctor_id" not in patient_cols:
        await conn.execute(
            text("ALTER TABLE patients ADD COLUMN preferred_doctor_id INTEGER")
        )
    if "last_interaction_summary" not in patient_cols:
        await conn.execute(
            text("ALTER TABLE patients ADD COLUMN last_interaction_summary TEXT")
        )

    await _apply_campaign_log_patches(conn)
    logger.info("[Schema] SQLite doctor scheduling columns verified")


def _patient_column_names(sync_conn) -> set[str]:
    inspector = inspect(sync_conn)
    if not inspector.has_table("patients"):
        return set()
    return {col["name"] for col in inspector.get_columns("patients")}


def _campaign_log_column_names(sync_conn) -> set[str]:
    inspector = inspect(sync_conn)
    if not inspector.has_table("campaign_logs"):
        return set()
    return {col["name"] for col in inspector.get_columns("campaign_logs")}


async def _apply_campaign_log_patches(conn: AsyncConnection) -> None:
    """Add outbound campaign columns to existing campaign_logs tables."""
    cols = await conn.run_sync(_campaign_log_column_names)
    if not cols:
        return

    if settings.uses_sqlite:
        await _apply_campaign_log_sqlite(conn, cols)
    else:
        await _apply_campaign_log_postgres(conn)
    logger.info("[Schema] campaign_logs columns verified")


async def _apply_campaign_log_postgres(conn: AsyncConnection) -> None:
    patches = [
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS preferred_language VARCHAR(8)",
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS message_template TEXT",
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS outbound_message TEXT",
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 3",
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS latency_ms DOUBLE PRECISION",
        "ALTER TABLE campaign_logs ADD COLUMN IF NOT EXISTS last_error TEXT",
    ]
    for stmt in patches:
        await conn.execute(text(stmt))


async def _apply_campaign_log_sqlite(conn: AsyncConnection, cols: set[str]) -> None:
    patches_sqlite = {
        "preferred_language": "ALTER TABLE campaign_logs ADD COLUMN preferred_language VARCHAR(8)",
        "message_template": "ALTER TABLE campaign_logs ADD COLUMN message_template TEXT",
        "outbound_message": "ALTER TABLE campaign_logs ADD COLUMN outbound_message TEXT",
        "retry_count": "ALTER TABLE campaign_logs ADD COLUMN retry_count INTEGER DEFAULT 0",
        "max_retries": "ALTER TABLE campaign_logs ADD COLUMN max_retries INTEGER DEFAULT 3",
        "latency_ms": "ALTER TABLE campaign_logs ADD COLUMN latency_ms REAL",
        "last_error": "ALTER TABLE campaign_logs ADD COLUMN last_error TEXT",
    }
    for col, stmt in patches_sqlite.items():
        if col not in cols:
            await conn.execute(text(stmt))
