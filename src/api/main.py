"""FastAPI application factory for AI-Powered SQL Analytics Copilot."""
import logging
from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.api.auth import verify_copilot_secret
from src.api.middleware import (
    RequestBodyLimitMiddleware,
    RequestIdMiddleware,
    RequestLoggingMiddleware,
)
from src.api.routes import router as analytics_router
from src.core.config import get_settings
from src.core.logging import setup_logging
from src.db.session import analytics_engine

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    # Initialize structured logging and sensitive data redaction
    setup_logging()

    settings = get_settings()
    is_prod = settings.is_production()

    # In production, disable interactive docs to prevent endpoint discovery by scrapers
    app = FastAPI(
        title="AI-Powered SQL Analytics Copilot API",
        description=(
            "Enterprise-grade Natural Language to SQL Analytics Copilot. "
            "Translates business questions into secure, read-only PostgreSQL queries with AST guardrails."
        ),
        version="0.1.0",
        docs_url="/docs" if not is_prod else None,
        redoc_url="/redoc" if not is_prod else None,
        openapi_url="/openapi.json" if not is_prod else None,
    )

    # Middlewares (added inside-out: first added is innermost, last added is outermost)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    # Include routes
    app.include_router(analytics_router)

    @app.get("/health", tags=["Health"])
    def health_check():
        """Liveness check verifying API process is running (public endpoint)."""
        return {"status": "healthy", "service": "ai-sql-analytics", "version": "0.1.0"}

    # In production, require authentication for /health/ready to prevent unauthenticated DB probing
    ready_dependencies = [Depends(verify_copilot_secret)] if is_prod else []

    @app.get("/health/ready", tags=["Health"], dependencies=ready_dependencies)
    def readiness_check(response: Response):
        """
        Readiness check verifying downstream dependency availability (PostgreSQL).
        Returns 200 OK if PostgreSQL is accessible, 503 Service Unavailable if not.
        """
        db_ready = False
        db_error = None
        try:
            with analytics_engine.connect() as conn:
                conn.execute(text("SELECT 1;"))
            db_ready = True
        except Exception as err:
            logger.warning("Readiness probe database check failed: %s", type(err).__name__)
            db_error = type(err).__name__

        settings = get_settings()
        llm_configured = bool(
            settings.GEMINI_API_KEY
            and settings.GEMINI_API_KEY != "your_gemini_api_key_here"
        )

        if db_ready:
            return {
                "status": "ready",
                "database": "connected",
                "llm_configured": llm_configured,
            }
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "not_ready",
                "database": "disconnected",
                "error": db_error or "Database connection failed",
                "llm_configured": llm_configured,
            }

    return app


app = create_app()
