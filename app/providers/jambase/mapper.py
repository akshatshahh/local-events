"""Translate a live JamBase /events payload into domain models.

Based on a live fetch on 2026-08-22 (Austin, 40 km, 8 events). JamBase uses
"" for missing values. Every accessor is defensive; one bad record cannot
fail a search.

Assumption: events without identifier or a parseable startDate are skipped
(cannot key or sort). Every other absent field stays None — not a fallback name.
"""

from __future__ import annotations

import logging
from datetime import date, time
from typing import Any

from app.models import CapacityBand, Event, GeoPoint, Performer, TicketOffer, Venue

logger = logging.getLogger(__name__)

PROVIDER_NAME = "jambase"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_time(value: Any) -> time | None:
    """Parse a time from a time string or from a datetime's clock portion.

    Live startDate values looked like "2026-08-22T20:00:00". Date-only strings
    have no time. Empty doorTime ("") is missing — we do not copy start time onto it.
    """
    text = _clean(value)
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[1]
    try:
        return time.fromisoformat(text[:8] if len(text) >= 8 else text)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


capacity_band = CapacityBand.from_capacity


def _region(address: dict[str, Any]) -> str | None:
    """Flatten addressRegion from either live shape.

    City payload: "addressRegion": "US-TX"
    Event venue:  "addressRegion": {"alternateName": "TX", "identifier": "US-TX", ...}
    """
    raw = address.get("addressRegion")
    if isinstance(raw, dict):
        return _clean(raw.get("alternateName")) or _iso_suffix(_clean(raw.get("identifier")))
    return _iso_suffix(_clean(raw))


def _country(address: dict[str, Any]) -> str | None:
    raw = address.get("addressCountry")
    if isinstance(raw, dict):
        return _clean(raw.get("identifier")) or _clean(raw.get("alternateName"))
    return _clean(raw)


def _iso_suffix(value: str | None) -> str | None:
    if value is None:
        return None
    return value.split("-")[-1] if "-" in value else value


def map_venue(raw: dict[str, Any] | None) -> Venue:
    raw = raw if isinstance(raw, dict) else {}
    address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    geo_raw = raw.get("geo") if isinstance(raw.get("geo"), dict) else {}

    geo = None
    lat, lng = geo_raw.get("latitude"), geo_raw.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        # (0, 0) is JamBase's "unknown" sentinel on some records, not a real venue.
        if not (lat == 0 and lng == 0):
            geo = GeoPoint(latitude=float(lat), longitude=float(lng))

    return Venue(
        name=_clean(raw.get("name")),
        city=_clean(address.get("addressLocality")),
        region=_region(address),
        country=_country(address),
        street_address=_clean(address.get("streetAddress")),
        postal_code=_clean(address.get("postalCode")),
        timezone=_clean(address.get("x-timezone")),
        geo=geo,
        capacity=_to_int(raw.get("maximumAttendeeCapacity")),
        url=_clean(raw.get("url")),
    )


def map_performer(raw: dict[str, Any]) -> Performer:
    genres = [g for g in (raw.get("genre") or []) if isinstance(g, str) and g.strip()]
    return Performer(
        name=_clean(raw.get("name")),
        is_headliner=bool(raw.get("x-isHeadliner")),
        genres=genres,
        image=_clean(raw.get("image")),
        url=_clean(raw.get("url")),
    )


def map_offer(raw: dict[str, Any]) -> TicketOffer | None:
    url = _clean(raw.get("url"))
    if not url:
        return None
    seller = raw.get("seller") if isinstance(raw.get("seller"), dict) else {}
    # priceSpecification was {} on the ZZ Top live record and is not shown.
    return TicketOffer(
        seller=_clean(seller.get("name")),
        url=url,
        is_primary=str(raw.get("category") or "").endswith("Primary"),
    )


def map_event(raw: dict[str, Any]) -> Event | None:
    identifier = _clean(raw.get("identifier"))
    start_date = _parse_date(raw.get("startDate"))
    if not identifier or not start_date:
        return None

    performers = [
        map_performer(p) for p in (raw.get("performer") or []) if isinstance(p, dict)
    ]
    offers = [
        offer
        for offer in (map_offer(o) for o in (raw.get("offers") or []) if isinstance(o, dict))
        if offer is not None
    ]
    genres = list(dict.fromkeys(g for p in performers for g in p.genres))

    return Event(
        id=identifier,
        provider=PROVIDER_NAME,
        title=_clean(raw.get("name")),
        start_date=start_date,
        start_time=_parse_time(raw.get("startDate")),
        end_date=_parse_date(raw.get("endDate")),
        door_time=_parse_time(raw.get("doorTime")),
        is_festival=raw.get("@type") == "Festival",
        status=_clean(raw.get("eventStatus")),
        url=_clean(raw.get("url")),
        image=_clean(raw.get("image")),
        venue=map_venue(raw.get("location") if isinstance(raw.get("location"), dict) else {}),
        performers=performers,
        offers=offers,
        genres=genres,
    )


def map_events(payload: dict[str, Any]) -> list[Event]:
    events: list[Event] = []
    for raw in payload.get("events") or []:
        if not isinstance(raw, dict):
            continue
        try:
            event = map_event(raw)
        except Exception:  # noqa: BLE001 - one bad record must not fail the search
            logger.exception("failed to map jambase event %s", raw.get("identifier"))
            continue
        if event:
            events.append(event)
    return events
