"""Application entrypoint and composition root."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.errors import AppError
from app.providers.jambase.provider import JamBaseProvider
from app.providers.registry import ProviderRegistry
from app.services.cache import TTLCache
from app.services.discovery import DiscoveryService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build providers once at startup; close their connection pools on exit."""
    settings = get_settings()
    registry = ProviderRegistry()

    # The only place a concrete provider is named. Adding a source is one line.
    registry.register(JamBaseProvider(settings))

    if not settings.jambase_api_key:
        logger.warning("JAMBASE_API_KEY is not set - event searches will fail.")

    app.state.registry = registry
    app.state.discovery = DiscoveryService(
        registry,
        TTLCache(
            ttl_seconds=settings.cache_ttl_seconds,
            max_entries=settings.cache_max_entries,
        ),
    )
    try:
        yield
    finally:
        await registry.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Local Events",
        description="Find upcoming events near you, with the context to choose between them.",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        """Our own errors carry a status and a stable code; everything else 500s."""
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    app.include_router(router)

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
