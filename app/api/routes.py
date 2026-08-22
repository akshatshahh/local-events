"""HTTP layer. Deliberately thin: parse, delegate, serialize."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_discovery
from app.errors import BadRequest
from app.models import EventSearch, ResolvedLocation, SearchResult
from app.services.discovery import DiscoveryService

router = APIRouter(prefix="/api")

DateWindow = Literal["today", "weekend", "week", "month", "all"]


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/locations/resolve",
    response_model=ResolvedLocation,
    summary="Resolve free text (or 'lat,lng') to a point on the map",
)
async def resolve_location(
    q: Annotated[str, Query(min_length=1, max_length=120, description="e.g. 'Austin, TX'")],
    discovery: Annotated[DiscoveryService, Depends(get_discovery)],
) -> ResolvedLocation:
    return await discovery.resolve_location(q)


@router.get(
    "/events",
    response_model=SearchResult,
    summary="Find upcoming events near a location",
)
async def search_events(
    discovery: Annotated[DiscoveryService, Depends(get_discovery)],
    location: Annotated[
        str, Query(min_length=1, max_length=120, description="City name or 'lat,lng'")
    ],
    radius_km: Annotated[float, Query(ge=1, le=500)] = 40,
    window: DateWindow = "month",
    genres: Annotated[list[str] | None, Query(description="Repeatable genre slug filter")] = None,
    q: Annotated[
        str | None, Query(max_length=120, description="Keyword match on event title")
    ] = None,
    sort: Literal["date", "distance"] = "date",
    limit: Annotated[int, Query(ge=1, le=60)] = 40,
) -> SearchResult:
    resolved = await discovery.resolve_location(location)
    date_from, date_to = _window_to_dates(window)

    search = EventSearch(
        location=resolved,
        radius_km=radius_km,
        date_from=date_from,
        date_to=date_to,
        genres=[g for g in (genres or []) if g.strip()],
        query=q,
        limit=limit,
    )
    return await discovery.search(search, sort=sort)


def _window_to_dates(window: DateWindow) -> tuple[date | None, date | None]:
    """Translate a human time window into an inclusive date range."""
    today = date.today()
    if window == "today":
        return today, today
    if window == "weekend":
        # Upcoming Fri-Sun; if it is already the weekend, start from today.
        days_to_friday = (4 - today.weekday()) % 7
        friday = today + timedelta(days=days_to_friday)
        if today.weekday() in (5, 6):
            friday = today - timedelta(days=today.weekday() - 4)
        return max(today, friday), friday + timedelta(days=2)
    if window == "week":
        return today, today + timedelta(days=7)
    if window == "month":
        return today, today + timedelta(days=30)
    if window == "all":
        return today, None
    raise BadRequest(f"Unknown window {window!r}")
