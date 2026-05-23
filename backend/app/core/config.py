import logging
from pathlib import Path
from typing import List, Self, Union

from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve absolute path to .env relative to this file (backend/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE_PATH = BASE_DIR / ".env"
SQLITE_DB_PATH = BASE_DIR / "twocare.db"
DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///./twocare.db"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH) if ENV_FILE_PATH.exists() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "2Care AI"
    ENV: str = "development"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"

    # Server Config
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Security
    SECRET_KEY: str = "supersecretkeythatisatleast32characterslongforsecurity"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days
    ALGORITHM: str = "HS256"

    # Database — SQLite by default; use postgresql+asyncpg://... in production/Docker
    DATABASE_URL: str = DEFAULT_SQLITE_URL

    # CORS Settings
    BACKEND_CORS_ORIGINS: Union[str, List[AnyHttpUrl]] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    @model_validator(mode="after")
    def apply_local_dev_sqlite_default(self) -> Self:
        """
        Local development never requires PostgreSQL.
        If .env still points at localhost Postgres, switch to SQLite automatically.
        (Docker Compose uses host `db`, not localhost — that URL is left unchanged.)
        """
        if self.ENV != "development":
            return self

        url = self.DATABASE_URL.strip().lower()
        is_postgres = url.startswith("postgresql") or url.startswith("postgres")
        is_local_postgres = is_postgres and (
            "localhost" in url or "127.0.0.1" in url
        )

        if is_local_postgres:
            logger.warning(
                "DATABASE_URL points at local PostgreSQL; using SQLite for development (%s)",
                DEFAULT_SQLITE_URL,
            )
            self.DATABASE_URL = DEFAULT_SQLITE_URL

        return self

    @property
    def uses_postgres(self) -> bool:
        url = self.DATABASE_URL.lower()
        return url.startswith("postgresql") or url.startswith("postgres")

    @property
    def uses_sqlite(self) -> bool:
        return not self.uses_postgres

    @property
    def async_database_url(self) -> str:
        """Async SQLAlchemy URL (postgresql+asyncpg or sqlite+aiosqlite)."""
        url = self.DATABASE_URL.strip()
        lower = url.lower()

        if lower.startswith("postgresql://") or lower.startswith("postgres://"):
            if "+asyncpg" not in lower:
                return url.replace("://", "+asyncpg://", 1)
            return url

        if lower.startswith("postgresql+asyncpg://"):
            return url

        if lower.startswith("sqlite+aiosqlite://"):
            return url

        if lower.startswith("sqlite://"):
            return url.replace("sqlite://", "sqlite+aiosqlite://", 1)

        return f"sqlite+aiosqlite:///{url.lstrip('/')}"

    @property
    def should_bootstrap_database(self) -> bool:
        """Auto-create tables and seed on startup (SQLite always; Postgres in dev)."""
        return self.uses_sqlite or self.ENV == "development"

    @property
    def database_label(self) -> str:
        return "SQLite" if self.uses_sqlite else "PostgreSQL"

    # External AI Services (multilingual voice features)
    DEEPGRAM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"
    # Set false to skip ElevenLabs entirely (text-only assistant responses)
    ENABLE_TTS: bool = True

    # Audio Streaming Config
    AUDIO_SAMPLE_RATE: int = 16000
    AUDIO_CHANNELS: int = 1
    AUDIO_CHUNK_SIZE: int = 1024


settings = Settings()
