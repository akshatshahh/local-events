"""The seam between the app and any upstream event source."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Event, EventSearch, ResolvedLocation


class EventProvider(ABC):
    """Contract every upstream event source must satisfy.

    Two responsibilities, deliberately kept separate:
      * `resolve_location` -- turn user text into a point on the map.
      * `search_events`    -- given a normalized query, return domain Events.

    Not every provider can geocode, so `supports_geocoding` lets the registry
    pick a capable one for step 1 while still fanning step 2 out to all.
    """

    name: str = "unknown"
    supports_geocoding: bool = False

    @abstractmethod
    async def search_events(self, search: EventSearch) -> list[Event]:
        """Return events matching `search`.

        Implementations should raise `ProviderUnavailable` / `ProviderAuthError`
        rather than returning partial garbage; the orchestrator isolates failures.
        """

    async def resolve_location(self, query: str) -> ResolvedLocation | None:
        """Geocode free text. Return None if this provider cannot resolve it."""
        return None

    async def find_cities(self, query: str) -> list[ResolvedLocation]:
        """All matching cities. Empty means none; more than one means the caller must ask the user."""
        location = await self.resolve_location(query)
        return [location] if location else []


    async def aclose(self) -> None:  # noqa: B027 - optional hook, not every provider holds resources
        """Release any long-lived resources (HTTP pools, etc.)."""
