import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import setup_logging
from app.api.v1.router import api_router
from app.core.database import Base, engine
from app.services.campaign_service import campaign_service
import app.models  # noqa: F401 — register ORM models for create_all

# Set up system-wide logger
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events manager for database connection & worker setup."""
    logger.info("Initializing system dependencies...")
    logger.info("Using %s database", settings.database_label)
    logger.info("DATABASE_URL (configured): %s", settings.DATABASE_URL)
    logger.info("DATABASE_URL (runtime engine): %s", settings.async_database_url)

    if settings.should_bootstrap_database:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                from app.db.migrations import apply_dev_schema_patches

                await apply_dev_schema_patches(conn)
            logger.info("Tables created successfully.")

            from app.core.database import AsyncSessionLocal
            from app.db.seed import run_startup_seed

            async with AsyncSessionLocal() as db:
                await run_startup_seed(db)
            logger.info("Doctor seed data loaded.")
        except Exception as e:
            logger.error("Database bootstrap failed: %s", e, exc_info=True)
    else:
        logger.info("Database bootstrap skipped (production PostgreSQL mode).")

    await campaign_service.start_worker()
    logger.info("Outbound campaign worker running.")

    yield

    await campaign_service.stop_worker()
    logger.info("Shutting down and cleaning up resources...")
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Set up Cross-Origin Resource Sharing (CORS) middleware
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include aggregate v1 API router
app.include_router(api_router, prefix=settings.API_V1_STR)


# Custom global exception handlers for API consistency
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled system error occurred: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal server error occurred. Please contact system administration."
        },
    )


@app.get("/health", status_code=status.HTTP_200_OK, tags=["system"])
async def health_check():
    """Health check endpoint for container orchestrators and load balancers."""
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "database": settings.database_label.lower(),
        "database_url": settings.DATABASE_URL,
        "async_database_url": settings.async_database_url,
    }
