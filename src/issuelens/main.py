"""FastAPI application entrypoint and lifespan lifecycle."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from issuelens import __version__
from issuelens.api.routes import router as operational_router
from issuelens.config import get_settings
from issuelens.core.errors import IssueLensError
from issuelens.core.logging import get_logger, setup_logging

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


def create_app() -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title="IssueLens API",
        description="Evidence-backed AI debugging assistant for GitHub bug reports",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url="/redoc" if settings.app_env != "production" else None,
    )

    # Register routers
    app.include_router(operational_router)

    @app.get("/", tags=["General"])
    def root() -> dict[str, Any]:
        """Root endpoint returning service identity."""
        return {
            "name": "IssueLens API",
            "version": __version__,
            "status": "online",
            "docs_url": "/docs" if settings.app_env != "production" else None,
        }

    # Exception Handlers
    @app.exception_handler(IssueLensError)
    async def issuelens_exception_handler(
        _request: Request, exc: IssueLensError
    ) -> JSONResponse:
        logger.warning("Domain error encountered: %s [code=%s]", exc.message, exc.code)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": exc.message,
                "code": exc.code,
            },
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
