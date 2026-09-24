"""Stripe Checkout in test mode. No card data is handled here."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import stripe
from stripe import APIConnectionError, RateLimitError, StripeError

from app.config import Settings
from app.reservations.service import FEE_CENTS
from app.services.retry import call_with_backoff

logger = logging.getLogger(__name__)

HOLD_SECONDS = 30 * 60


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str
    idempotency_key: str


def checkout_idempotency_key(reservation_id: str) -> str:
    """Stable for one reservation, different for another. Stripe dedupes on this."""
    return reservation_id


def is_retryable_stripe_error(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, StripeError):
        status = exc.http_status or 0
        return status == 429 or status >= 500
    return False


async def create_checkout_session(
    settings: Settings,
    *,
    reservation_id: str,
    event_id: str,
    email: str,
    quantity: int,
) -> CheckoutSession:
    key = checkout_idempotency_key(reservation_id)
    base = settings.base_url.rstrip("/")
    params = {
        "api_key": settings.stripe_secret_key,
        "max_network_retries": 0,
        "idempotency_key": key,
        "mode": "payment",
        "customer_email": email,
        "client_reference_id": reservation_id,
        "expires_at": int(time.time()) + HOLD_SECONDS,
        "success_url": (
            f"{base}/reservations/success?reservation_id={reservation_id}"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": f"{base}/reservations/cancel?reservation_id={reservation_id}",
        "metadata": {"reservation_id": reservation_id, "event_id": event_id},
        "line_items": [
            {
                "quantity": quantity,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": FEE_CENTS,
                    "product_data": {
                        "name": "Spot reservation fee (test mode, no real charge)",
                    },
                },
            }
        ],
    }

    async def attempt() -> stripe.checkout.Session:
        return await stripe.checkout.Session.create_async(**params)

    created = await call_with_backoff(
        attempt,
        is_retryable=is_retryable_stripe_error,
        max_retries=settings.http_max_retries,
    )
    if not created.url or not created.id:
        raise StripeError("Stripe Checkout did not return a url.")
    logger.info("reservation_id=%s outcome=checkout_created", reservation_id)
    return CheckoutSession(id=created.id, url=created.url, idempotency_key=key)
