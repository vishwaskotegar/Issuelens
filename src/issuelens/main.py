"""FastAPI application entrypoint and lifespan lifecycle."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from issuelens import __version__
from issuelens.api.routes import router as operational_router
from issuelens.config import Settings, get_settings
from issuelens.core.errors import IssueLensError, RateLimitExceededError
from issuelens.core.logging import get_logger, setup_logging
from issuelens.persistence.db import close_engine

logger = get_logger("issuelens.main")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context for startup and shutdown hooks."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "IssueLens application starting up [env=%s, version=%s]",
        settings.app_env,
        __version__,
    )
    yield
    logger.info("IssueLens application shutting down")
    await close_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    app_settings = settings or get_settings()
    app = FastAPI(
        title="IssueLens API",
        description="Evidence-backed AI debugging assistant for GitHub bug reports",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if app_settings.app_env != "production" else None,
        redoc_url="/redoc" if app_settings.app_env != "production" else None,
    )

    # CORS Protection
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # HTTP Security Headers
    @app.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        if request.url.scheme == "https" or app_settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # Register routers
    app.include_router(operational_router)

    @app.get("/", tags=["General"])
    def root() -> dict[str, Any]:
        """Root endpoint returning service identity."""
        return {
            "name": "IssueLens API",
            "version": __version__,
            "status": "online",
            "docs_url": "/docs" if app_settings.app_env != "production" else None,
        }

    # Exception Handlers
    @app.exception_handler(IssueLensError)
    async def issuelens_exception_handler(
        _request: Request, exc: IssueLensError
    ) -> JSONResponse:
        logger.warning(
            "Domain error encountered: %s [code=%s, status=%d]",
            exc.message,
            exc.code,
            exc.status_code,
        )
        headers = {}
        if isinstance(exc, RateLimitExceededError):
            retry_after = exc.retry_after_seconds if exc.retry_after_seconds else 60
            headers["Retry-After"] = str(max(1, int(retry_after)))
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.message,
                "code": exc.code,
            },
            headers=headers if headers else None,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled server exception: %s", str(exc), exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "An internal error occurred. Please check server logs.",
                "code": "INTERNAL_SERVER_ERROR",
            },
        )

    return app


app = create_app()
