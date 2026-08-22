"""Orchestration against fake providers — these must hold when a 2nd source is added."""

from datetime import date, timedelta

from app.errors import ProviderUnavailable
from app.models import EventSearch, GeoPoint, ResolvedLocation
from app.providers.base import EventProvider
from app.providers.registry import ProviderRegistry
from app.services.cache import TTLCache
from app.services.discovery import DiscoveryService
from tests.factories import headliner, make_event

AUSTIN = ResolvedLocation(
    label="Austin, TX", geo=GeoPoint(latitude=30.30, longitude=-97.75), city="Austin"
)


class FakeProvider(EventProvider):
    def __init__(self, name, events=None, error=None):
        self.name = name
        self._events = events or []
        self._error = error
        self.calls = 0

    async def search_events(self, search):
        self.calls += 1
        if self._error:
            raise self._error
        return list(self._events)


def build(*providers):
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    return DiscoveryService(registry, TTLCache(ttl_seconds=300))


def a_search():
    return EventSearch(location=AUSTIN, radius_km=40)


async def test_one_provider_failing_does_not_fail_the_search():
    good = FakeProvider("good", [make_event(id="a:1", title="Kept")])
    bad = FakeProvider("bad", error=ProviderUnavailable("upstream down"))
    result = await build(good, bad).search(a_search())
    assert [e.title for e in result.events] == ["Kept"]
    statuses = {p.name: p for p in result.providers}
    assert statuses["good"].ok is True
    assert statuses["bad"].ok is False


async def test_same_show_from_two_providers_is_deduplicated():
    when = date.today() + timedelta(days=5)
    acts = [headliner("The Strokes")]
    a = make_event(id="a:1", provider="a", venue="The Fillmore", start=when, performers=acts)
    b = make_event(id="b:9", provider="b", venue="Fillmore SF", start=when, performers=acts)
    result = await build(FakeProvider("a", [a]), FakeProvider("b", [b])).search(a_search())
    assert len(result.events) == 1


async def test_results_are_cached_per_provider():
    provider = FakeProvider("p", [make_event()])
    service = build(provider)
    first = await service.search(a_search())
    second = await service.search(a_search())
    assert provider.calls == 1
    assert first.providers[0].cached is False
    assert second.providers[0].cached is True
