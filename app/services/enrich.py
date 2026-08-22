"""Derived signals we compute from values we already have.

Approved: haversine distance, and capacity-band labels when capacity is present.
Rejected: rare-visit (any threshold on x-numUpcomingEvents).
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.models import CapacityBand, Event, GeoPoint

EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.609344

CAPACITY_LABELS = {
    CapacityBand.INTIMATE: "Intimate venue",
    CapacityBand.SMALL: "Small venue",
    CapacityBand.MIDSIZE: "Midsize venue",
    CapacityBand.LARGE: "Arena",
    CapacityBand.STADIUM: "Stadium",
}


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _local_today(timezone: str | None) -> date:
    if timezone:
        try:
            return datetime.now(ZoneInfo(timezone)).date()
        except Exception:  # noqa: BLE001 - unknown tz string; fall back to UTC
            pass
    return datetime.now(UTC).date()


def enrich(event: Event, origin: GeoPoint | None) -> Event:
    signals = event.signals

    if origin and event.venue.geo:
        km = haversine_km(origin, event.venue.geo)
        signals.distance_km = round(km, 1)
        signals.distance_mi = round(km / KM_PER_MILE, 1)

    today = _local_today(event.venue.timezone)
    signals.days_away = (event.start_date - today).days
    signals.lineup_size = len(event.performers)
    signals.has_tickets = bool(event.offers)
    signals.tags = _build_tags(event)
    return event


def _build_tags(event: Event) -> list[str]:
    tags: list[str] = []
    if event.is_festival:
        tags.append("Festival")
    if label := CAPACITY_LABELS.get(event.venue.capacity_band):
        tags.append(label)
    if event.status and event.status.lower() not in ("scheduled",):
        tags.append(event.status.replace("-", " ").title())
    return tags
