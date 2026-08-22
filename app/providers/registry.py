"""Holds the set of providers the app fans out to.

Registration is explicit and happens once at startup (see app.main), which keeps
provider construction (and credential checks) out of request handling.
"""

from __future__ import annotations

from app.providers.base import EventProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, EventProvider] = {}

    def register(self, provider: EventProvider) -> None:
        self._providers[provider.name] = provider

    def all(self) -> list[EventProvider]:
        return list(self._providers.values())

    def get(self, name: str) -> EventProvider | None:
        return self._providers.get(name)

    def geocoder(self) -> EventProvider | None:
        """First registered provider able to turn text into coordinates."""
        return next((p for p in self._providers.values() if p.supports_geocoding), None)

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
