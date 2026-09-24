"""Application entrypoint and composition root."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.reservations import router as reservations_router
from app.api.routes import router
from app.config import get_settings, payments_enabled
from app.db import build_engine, init_models
from app.errors import AmbiguousLocation, AppError
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
    # Raises if a live key or a half-configured Stripe pair is set.
    app.state.payments_enabled = payments_enabled(settings)
    engine = build_engine(settings.database_url)
    await init_models(engine)
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

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
    if not app.state.payments_enabled:
        logger.info("stripe outcome=disabled")
    try:
        yield
    finally:
        await registry.aclose()
        await engine.dispose()


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
        content: dict = {"error": {"code": exc.code, "message": exc.message}}
        if isinstance(exc, AmbiguousLocation):
            content["error"]["candidates"] = [c.model_dump() for c in exc.candidates]
        return JSONResponse(status_code=exc.status_code, content=content)

    app.include_router(router)
    app.include_router(reservations_router)

    @app.get("/reservations/success", include_in_schema=False)
    async def reservation_success() -> FileResponse:
        return FileResponse(WEB_DIR / "reservation-success.html")

    @app.get("/reservations/cancel", include_in_schema=False)
    async def reservation_cancel() -> FileResponse:
        return FileResponse(WEB_DIR / "reservation-cancel.html")

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


app = create_app()
