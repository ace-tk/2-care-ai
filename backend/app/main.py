import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.core.config import settings
from backend.app.core.logging import setup_logging
from backend.app.api.v1.router import api_router
from backend.app.core.database import Base, engine
from backend.app.services.campaign_service import campaign_service

# Set up system-wide logger
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events manager for database connection & worker setup."""
    logger.info("Initializing system dependencies...")
    
    # Development database schema auto-creation placeholder
    # In production, Alembic migrations should be used exclusively.
    if settings.ENV == "development":
        logger.info("Dev Mode: Auto-creating database tables if they do not exist...")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables verified.")
        except Exception as e:
            logger.error(f"Failed to auto-create database tables: {e}")
            
    # Start background workers
    await campaign_service.start_worker()
            
    yield
    
    logger.info("Shutting down and cleaning up resources...")
    await campaign_service.stop_worker()
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
    return {"status": "healthy", "project": settings.PROJECT_NAME}
