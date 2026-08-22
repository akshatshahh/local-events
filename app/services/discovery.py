"""Orchestrates a search across every registered provider.

Responsibilities kept here (and out of both routes and providers):
  * cache lookup / population
  * concurrent fan-out with per-provider failure isolation
  * cross-provider de-duplication
  * enrichment, ranking and facet counts

This is the layer that makes "add a 10th provider" a registration change
rather than a rewrite: nothing below is JamBase-specific.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from collections import Counter
from datetime import time

from app.errors import AppError, LocationNotFound
from app.models import (
    Event,
    EventSearch,
    ProviderStatus,
    ResolvedLocation,
    SearchResult,
)
from app.providers.registry import ProviderRegistry
from app.services.cache import TTLCache
from app.services.enrich import enrich

logger = logging.getLogger(__name__)

SORT_OPTIONS = ("date", "distance")


class DiscoveryService:
    def __init__(self, registry: ProviderRegistry, cache: TTLCache):
        self._registry = registry
        self._cache = cache

    # -- location ---------------------------------------------------------

    async def resolve_location(self, query: str) -> ResolvedLocation:
        key = f"loc:{query.strip().lower()}"
        if cached := self._cache.get(key):
            return cached

        geocoder = self._registry.geocoder()
        if geocoder is None:
            raise LocationNotFound("No provider can resolve locations.")

        location = await geocoder.resolve_location(query)
        if location is None:
            raise LocationNotFound(
                f"Could not find a place matching {query!r}. "
                "Try 'City, ST' or a 'lat,lng' pair."
            )
        self._cache.set(key, location)
        return location

    # -- search -----------------------------------------------------------

    async def search(self, search: EventSearch, sort: str = "date") -> SearchResult:
        events, statuses = await self._gather(search)

        events = _deduplicate(events)
        origin = search.location.geo
        events = [enrich(event, origin) for event in events]

        # Facets are counted before the genre filter is applied upstream, so
        # they reflect what is actually in the returned set.
        facets = Counter(genre for event in events for genre in event.genres)

        events = _sort_events(events, sort)
        return SearchResult(
            location=search.location,
            events=events[: search.limit],
            providers=statuses,
            genre_facets=dict(facets.most_common(20)),
            total_available=len(events),
        )

    async def _gather(
        self, search: EventSearch
    ) -> tuple[list[Event], list[ProviderStatus]]:
        providers = self._registry.all()
        results = await asyncio.gather(
            *(self._fetch_one(p, search) for p in providers),
            return_exceptions=False,  # _fetch_one never raises
        )
        events: list[Event] = []
        statuses: list[ProviderStatus] = []
        for status, provider_events in results:
            statuses.append(status)
            events.extend(provider_events)
        return events, statuses

    async def _fetch_one(self, provider, search: EventSearch):
        """Fetch from one provider, converting any failure into a status.

        One provider being down must degrade the result set, not the request.
        """
        key = _cache_key(provider.name, search)
        if (cached := self._cache.get(key)) is not None:
            return (
                ProviderStatus(
                    name=provider.name, ok=True, event_count=len(cached), cached=True
                ),
                cached,
            )
        try:
            events = await provider.search_events(search)
        except AppError as exc:
            logger.warning("provider %s failed: %s", provider.name, exc.message)
            return ProviderStatus(name=provider.name, ok=False, error=exc.message), []
        except Exception as exc:  # noqa: BLE001 - never let one provider 500 the app
            logger.exception("provider %s raised unexpectedly", provider.name)
            return (
                ProviderStatus(name=provider.name, ok=False, error=str(exc)),
                [],
            )

        self._cache.set(key, events)
        return (
            ProviderStatus(name=provider.name, ok=True, event_count=len(events)),
            events,
        )


def _cache_key(provider_name: str, search: EventSearch) -> str:
    payload = search.model_dump_json()
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"ev:{provider_name}:{digest}"


_PUNCT = re.compile(r"[^a-z0-9]+")


def _normalize_title(text: str) -> str:
    """Fold case, accents and punctuation so near-identical titles collide."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _PUNCT.sub(" ", folded).strip()


def _dedupe_key(event: Event) -> tuple:
    """Identity of a real-world event, independent of who listed it.

    Same night + same city + same headline act is the same show. Venue names
    vary too much between sources ("The Fillmore" vs "Fillmore SF") to key on.
    """
    headliner = event.headliner
    act = _normalize_title((headliner.name if headliner else None) or event.title or "")
    city = _normalize_title(event.venue.city or "")
    return (event.start_date, city, act)


def _deduplicate(events: list[Event]) -> list[Event]:
    """Collapse duplicates, keeping the richest record of each show."""
    best: dict[tuple, Event] = {}
    for event in events:
        key = _dedupe_key(event)
        incumbent = best.get(key)
        if incumbent is None or _completeness(event) > _completeness(incumbent):
            best[key] = event
    return list(best.values())


def _completeness(event: Event) -> int:
    """Crude richness score used to pick a winner among duplicates."""
    return sum(
        (
            bool(event.image),
            bool(event.offers),
            bool(event.venue.geo),
            bool(event.venue.capacity),
            bool(event.genres),
            len(event.performers) > 0,
        )
    )


def _sort_events(events: list[Event], sort: str) -> list[Event]:
    if sort == "distance":
        # Events with no coordinates sink to the bottom rather than sorting as 0.
        return sorted(
            events,
            key=lambda e: (
                e.signals.distance_km is None,
                e.signals.distance_km or 0.0,
                e.start_date,
            ),
        )
    return sorted(events, key=lambda e: (e.start_date, e.start_time or time.min, e.signals.distance_km or 1e9))
