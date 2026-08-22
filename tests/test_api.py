"""HTTP contract through FastAPI. JamBase is mocked — no network, no real key."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

BASE = "https://api.data.jambase.com/v3"


@pytest.fixture
def client(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("JAMBASE_API_KEY", "test-key")
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@respx.mock
def test_search_returns_enriched_events(client, jambase_payload, jambase_cities):
    cities = respx.get(f"{BASE}/geographies/cities").mock(
        return_value=httpx.Response(200, json=jambase_cities)
    )
    respx.get(f"{BASE}/events").mock(
        return_value=httpx.Response(200, json=jambase_payload)
    )

    res = client.get("/api/events", params={"location": "Austin, TX"})
    assert res.status_code == 200
    assert cities.calls[0].request.headers["authorization"] == "Bearer test-key"

    body = res.json()
    assert body["location"]["label"] == "Austin, TX"
    assert body["providers"][0]["ok"] is True
    assert [e["title"] for e in body["events"]] == [
        "Dirty Heads at Germania Insurance Amphitheater",
        "ZZ Top at ACL Live at The Moody Theater",
    ]
    zz = body["events"][1]
    assert zz["start_time"] == "20:00:00"
    assert zz["signals"]["distance_km"] is not None
    assert "Midsize venue" in zz["signals"]["tags"]
    assert "price_from" not in zz["offers"][0]


@respx.mock
def test_unknown_location_is_404(client):
    respx.get(f"{BASE}/geographies/cities").mock(
        return_value=httpx.Response(200, json={"cities": []})
    )
    res = client.get("/api/events", params={"location": "Zzzyxqq"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "location_not_found"


@respx.mock
def test_rejected_key_is_502(client):
    respx.get(f"{BASE}/geographies/cities").mock(return_value=httpx.Response(403))
    res = client.get("/api/events", params={"location": "Austin, TX"})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "provider_auth_error"


@respx.mock
def test_upstream_5xx_is_retried_then_degrades(client, jambase_cities):
    respx.get(f"{BASE}/geographies/cities").mock(
        return_value=httpx.Response(200, json=jambase_cities)
    )
    events = respx.get(f"{BASE}/events").mock(return_value=httpx.Response(503))
    res = client.get("/api/events", params={"location": "Austin, TX"})
    assert res.status_code == 200
    assert res.json()["events"] == []
    assert res.json()["providers"][0]["ok"] is False
    assert events.call_count == 3


@respx.mock
def test_client_error_is_not_retried(client, jambase_cities):
    respx.get(f"{BASE}/geographies/cities").mock(
        return_value=httpx.Response(200, json=jambase_cities)
    )
    events = respx.get(f"{BASE}/events").mock(return_value=httpx.Response(400))
    client.get("/api/events", params={"location": "Austin, TX"})
    assert events.call_count == 1


@respx.mock
def test_near_me_keeps_the_exact_coordinates(client, jambase_payload, jambase_cities):
    respx.get(f"{BASE}/geographies/cities").mock(
        return_value=httpx.Response(200, json=jambase_cities)
    )
    respx.get(f"{BASE}/events").mock(
        return_value=httpx.Response(200, json=jambase_payload)
    )
    res = client.get("/api/events", params={"location": "34.05,-118.24"})
    assert res.status_code == 200
    assert res.json()["location"]["geo"] == {"latitude": 34.05, "longitude": -118.24}
