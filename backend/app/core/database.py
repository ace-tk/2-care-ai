from typing import Any, AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


def _build_engine_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "echo": settings.DEBUG,
        "future": True,
    }

    if settings.uses_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True

    return kwargs


engine = create_async_engine(
    settings.async_database_url,
    **_build_engine_kwargs(),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy database models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions during API requests."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
