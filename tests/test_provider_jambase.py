"""JamBase query dialect — wrong param names silently return empty pages."""

from datetime import date

import httpx
import pytest
import respx

from app.config import Settings
from app.models import EventSearch, GeoPoint, ResolvedLocation
from app.providers.jambase.provider import JamBaseProvider

BASE = "https://api.data.jambase.com/v3"
AUSTIN = ResolvedLocation(label="Austin, TX", geo=GeoPoint(latitude=30.30, longitude=-97.75))


@pytest.fixture
def provider():
    return JamBaseProvider(Settings(jambase_api_key="k", jambase_base_url=BASE))


@respx.mock
async def test_search_translates_query_parameters(provider):
    route = respx.get(f"{BASE}/events").mock(return_value=httpx.Response(200, json={"events": []}))
    await provider.search_events(
        EventSearch(
            location=AUSTIN,
            radius_km=25,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
            genres=["rock", "blues"],
            limit=20,
        )
    )
    params = route.calls[0].request.url.params
    assert params["geoLatitude"] == "30.3"
    assert params["geoRadiusAmount"] == "25.0"
    assert params["geoRadiusUnits"] == "km"
    assert params["eventDateFrom"] == "2026-09-01"
    assert params["genreSlug"] == "rock|blues"


@respx.mock
async def test_city_and_state_are_parsed_from_free_text(provider):
    route = respx.get(f"{BASE}/geographies/cities").mock(
        return_value=httpx.Response(200, json={"cities": []})
    )
    await provider.resolve_location("Austin, TX")
    params = route.calls[0].request.url.params
    assert params["geoCityName"] == "Austin"
    assert params["geoStateIso"] == "US-TX"
