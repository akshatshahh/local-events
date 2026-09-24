"""Reservation holds, Checkout idempotency, and webhook state. Stripe is mocked."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import build_engine, init_models
from app.errors import CapacityExceeded
from app.main import create_app
from app.reservations.models import EventHold
from app.reservations.service import FEE_CENTS, create_pending
from app.reservations.stripe_checkout import (
    checkout_idempotency_key,
    is_retryable_stripe_error,
)
from app.services.retry import call_with_backoff

SECRET = "whsec_test_secret"
KEY = "sk_test_local_events"


def sign(payload: bytes, secret: str = SECRET) -> str:
    timestamp = int(time.time())
    signed = f"{timestamp}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _db_path() -> str:
    url = get_settings().database_url
    return url.split("///", 1)[1]


def _reservations() -> list[tuple]:
    conn = sqlite3.connect(_db_path())
    try:
        return conn.execute(
            "SELECT id, status, event_id, quantity FROM reservations ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()


def _holds() -> dict[str, int]:
    conn = sqlite3.connect(_db_path())
    try:
        return dict(conn.execute("SELECT event_id, held_quantity FROM event_holds"))
    finally:
        conn.close()


def _processed() -> list[str]:
    conn = sqlite3.connect(_db_path())
    try:
        rows = conn.execute("SELECT stripe_event_id FROM processed_webhook_events")
        return [row[0] for row in rows]
    finally:
        conn.close()


@pytest.fixture
def payments_client(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.setenv("JAMBASE_API_KEY", "test-key")
    monkeypatch.setenv("STRIPE_SECRET_KEY", KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("RESERVATION_CAP_PER_EVENT", "10")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/reservations.db")
    monkeypatch.setattr("app.services.retry.asyncio.sleep", _no_sleep)
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


async def _no_sleep(_delay: float) -> None:
    return None


def _fake_checkout(**kwargs):
    key = kwargs["idempotency_key"]
    return SimpleNamespace(
        id="cs_" + key[:8],
        url="https://checkout.stripe.com/c/pay/cs_test_" + key[:8],
    )


async def test_retry_on_5xx_not_on_4xx():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise stripe.APIError("down", None, 500)
        return "ok"

    assert is_retryable_stripe_error(stripe.APIError("down", None, 500))
    assert is_retryable_stripe_error(stripe.APIConnectionError("net"))
    assert not is_retryable_stripe_error(stripe.InvalidRequestError("bad", None, 400))

    assert (
        await call_with_backoff(flaky, is_retryable=is_retryable_stripe_error, sleep=_no_sleep)
        == "ok"
    )
    assert calls["n"] == 3

    once = {"n": 0}

    async def rejected():
        once["n"] += 1
        raise stripe.InvalidRequestError("bad", None, 400)

    with pytest.raises(stripe.InvalidRequestError):
        await call_with_backoff(
            rejected, is_retryable=is_retryable_stripe_error, sleep=_no_sleep
        )
    assert once["n"] == 1


def test_idempotency_key_is_stable_per_reservation():
    assert checkout_idempotency_key("abc") == checkout_idempotency_key("abc")
    assert checkout_idempotency_key("abc") != checkout_idempotency_key("def")


def test_live_key_refuses_startup(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_nope")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/live.db")
    with pytest.raises(RuntimeError, match="sk_test_"):
        with TestClient(create_app()):
            pass
    get_settings.cache_clear()


def test_reservations_off_without_stripe_keys(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.setenv("JAMBASE_API_KEY", "test-key")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/off.db")
    with TestClient(create_app()) as client:
        res = client.post(
            "/api/reservations",
            json={"event_id": "evt", "email": "a@example.com", "quantity": 1},
        )
    assert res.status_code == 503
    assert "Test mode: no real charges." in res.json()["error"]["message"]
    get_settings.cache_clear()


async def test_parallel_requests_cannot_oversell(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/cap.db")
    await init_models(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def one(i: int) -> str:
        session = factory()
        try:
            await create_pending(
                session,
                event_id="show-1",
                email=f"p{i}@example.com",
                quantity=1,
                cap=2,
            )
            return "ok"
        except CapacityExceeded:
            return "full"
        finally:
            await session.close()

    results = await asyncio.gather(*[one(i) for i in range(8)])
    assert results.count("ok") == 2
    session = factory()
    try:
        hold = await session.get(EventHold, "show-1")
        assert hold is not None
        assert hold.held_quantity == 2
    finally:
        await session.close()
        await engine.dispose()


def test_checkout_idempotency_key_matches_reservation(payments_client, monkeypatch):
    seen: list[dict] = []

    async def fake(**kwargs):
        seen.append(kwargs)
        return _fake_checkout(**kwargs)

    monkeypatch.setattr(stripe.checkout.Session, "create_async", fake)
    first = payments_client.post(
        "/api/reservations",
        json={"event_id": "evt-a", "email": "a@example.com", "quantity": 1},
    )
    second = payments_client.post(
        "/api/reservations",
        json={"event_id": "evt-a", "email": "b@example.com", "quantity": 2},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert seen[0]["idempotency_key"] == seen[0]["metadata"]["reservation_id"]
    assert seen[1]["idempotency_key"] == seen[1]["metadata"]["reservation_id"]
    assert seen[0]["idempotency_key"] != seen[1]["idempotency_key"]
    assert seen[0]["line_items"][0]["price_data"]["unit_amount"] == FEE_CENTS
    assert first.json()["checkout_url"].startswith("https://checkout.stripe.com/")


def test_stripe_4xx_does_not_retry_and_releases_hold(payments_client, monkeypatch):
    calls = {"n": 0}

    async def fake(**kwargs):
        calls["n"] += 1
        raise stripe.InvalidRequestError("bad", None, 400)

    monkeypatch.setattr(stripe.checkout.Session, "create_async", fake)
    res = payments_client.post(
        "/api/reservations",
        json={"event_id": "evt-b", "email": "a@example.com", "quantity": 1},
    )
    assert res.status_code == 502
    assert calls["n"] == 1
    assert _holds().get("evt-b", 0) == 0
    assert _reservations()[0][1] == "canceled"


def test_stripe_5xx_is_retried(payments_client, monkeypatch):
    calls = {"n": 0}

    async def fake(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise stripe.APIConnectionError("net")
        return _fake_checkout(**kwargs)

    monkeypatch.setattr(stripe.checkout.Session, "create_async", fake)
    res = payments_client.post(
        "/api/reservations",
        json={"event_id": "evt-c", "email": "a@example.com", "quantity": 1},
    )
    assert res.status_code == 200
    assert calls["n"] == 3


def _event(event_id: str, event_type: str, reservation_id: str) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": event_type,
            "data": {
                "object": {
                    "id": "cs_test_1",
                    "object": "checkout.session",
                    "metadata": {"reservation_id": reservation_id},
                }
            },
        }
    ).encode()


def _post_event(client: TestClient, body: bytes, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post("/api/stripe/webhook", content=body, headers=headers)


def _reserve(client: TestClient, monkeypatch, event_id: str = "evt-d") -> str:
    async def fake(**kwargs):
        return _fake_checkout(**kwargs)

    monkeypatch.setattr(stripe.checkout.Session, "create_async", fake)
    res = client.post(
        "/api/reservations",
        json={"event_id": event_id, "email": "a@example.com", "quantity": 2},
    )
    assert res.status_code == 200
    return _reservations()[-1][0]


def test_webhook_rejects_bad_signature(payments_client, monkeypatch):
    reservation_id = _reserve(payments_client, monkeypatch)
    body = _event("evt_1", "checkout.session.completed", reservation_id)
    missing = _post_event(payments_client, body, None)
    invalid = _post_event(payments_client, body, "t=1,v1=deadbeef")
    assert missing.status_code == 400
    assert invalid.status_code == 400
    assert _processed() == []
    status = payments_client.get(f"/api/reservations/{reservation_id}")
    assert status.json()["status"] == "pending"
    assert status.json()["test_mode"] is True


def test_completed_confirms_and_replay_is_a_noop(payments_client, monkeypatch):
    reservation_id = _reserve(payments_client, monkeypatch, "evt-e")
    body = _event("evt_complete", "checkout.session.completed", reservation_id)
    first = _post_event(payments_client, body, sign(body))
    second = _post_event(payments_client, body, sign(body))
    assert first.status_code == 200
    assert second.status_code == 200
    assert _processed() == ["evt_complete"]
    status = payments_client.get(f"/api/reservations/{reservation_id}").json()["status"]
    assert status == "confirmed"
    assert _holds()["evt-e"] == 2


def test_expired_releases_capacity(payments_client, monkeypatch):
    monkeypatch.setenv("RESERVATION_CAP_PER_EVENT", "2")
    get_settings.cache_clear()
    reservation_id = _reserve(payments_client, monkeypatch, "evt-f")
    assert _holds()["evt-f"] == 2
    body = _event("evt_expire", "checkout.session.expired", reservation_id)
    res = _post_event(payments_client, body, sign(body))
    assert res.status_code == 200
    assert payments_client.get(f"/api/reservations/{reservation_id}").json()["status"] == "expired"
    assert _holds()["evt-f"] == 0


def test_expired_after_completed_does_not_release(payments_client, monkeypatch):
    reservation_id = _reserve(payments_client, monkeypatch, "evt-g")
    done = _event("evt_done", "checkout.session.completed", reservation_id)
    late = _event("evt_late", "checkout.session.expired", reservation_id)
    assert _post_event(payments_client, done, sign(done)).status_code == 200
    assert _post_event(payments_client, late, sign(late)).status_code == 200
    status = payments_client.get(f"/api/reservations/{reservation_id}").json()["status"]
    assert status == "confirmed"
    assert _holds()["evt-g"] == 2


def test_unknown_event_type_is_ignored(payments_client, monkeypatch):
    reservation_id = _reserve(payments_client, monkeypatch, "evt-h")
    body = _event("evt_other", "charge.refunded", reservation_id)
    res = _post_event(payments_client, body, sign(body))
    assert res.status_code == 200
    assert payments_client.get(f"/api/reservations/{reservation_id}").json()["status"] == "pending"
    assert _processed() == ["evt_other"]
