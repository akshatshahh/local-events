"""Reserve a spot. Discovery routes are not in this file."""

from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import BadRequest, CheckoutFailed, NotFound, PaymentsNotConfigured
from app.reservations.models import Reservation
from app.reservations.service import (
    apply_webhook,
    attach_session,
    cancel_pending,
    create_pending,
)
from app.reservations.stripe_checkout import create_checkout_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_HANDLED = {
    "checkout.session.completed": "confirm",
    "checkout.session.expired": "expire",
}


class ReservationIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=512)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    quantity: int = Field(ge=1, le=4)


class ReservationOut(BaseModel):
    id: str
    event_id: str
    status: str
    quantity: int
    amount_cents: int
    test_mode: bool = True


def _require_payments(request: Request) -> None:
    if not request.app.state.payments_enabled:
        raise PaymentsNotConfigured(
            "Reservations are off. Set STRIPE_SECRET_KEY (sk_test_...) and "
            "STRIPE_WEBHOOK_SECRET. Test mode: no real charges."
        )


def _session(request: Request) -> AsyncSession:
    return request.app.state.session_factory()


@router.post("/reservations", summary="Hold spots and open a test-mode Checkout session")
async def start_reservation(body: ReservationIn, request: Request) -> dict[str, str]:
    _require_payments(request)
    settings = get_settings()
    session = _session(request)
    try:
        reservation = await create_pending(
            session,
            event_id=body.event_id,
            email=body.email,
            quantity=body.quantity,
            cap=settings.reservation_cap_per_event,
        )
        try:
            checkout = await create_checkout_session(
                settings,
                reservation_id=reservation.id,
                event_id=body.event_id,
                email=body.email,
                quantity=body.quantity,
            )
        except Exception:
            logger.warning("reservation_id=%s outcome=checkout_failed", reservation.id)
            await cancel_pending(session, reservation.id)
            raise CheckoutFailed(
                "Checkout could not be started. The hold was released. "
                "Test mode: no real charges."
            ) from None
        await attach_session(session, reservation.id, checkout.id)
        return {"checkout_url": checkout.url}
    finally:
        await session.close()


@router.get(
    "/reservations/{reservation_id}",
    response_model=ReservationOut,
    summary="Reservation status for the success page",
)
async def get_reservation(reservation_id: str, request: Request) -> ReservationOut:
    _require_payments(request)
    session = _session(request)
    try:
        async with session.begin():
            reservation = await session.get(Reservation, reservation_id)
        if reservation is None:
            raise NotFound("Unknown reservation.")
        return ReservationOut(
            id=reservation.id,
            event_id=reservation.event_id,
            status=reservation.status,
            quantity=reservation.quantity,
            amount_cents=reservation.amount_cents,
        )
    finally:
        await session.close()


@router.post("/stripe/webhook", summary="Stripe webhook. Test mode only.")
async def stripe_webhook(request: Request) -> dict[str, bool]:
    _require_payments(request)
    settings = get_settings()
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    if not signature:
        raise BadRequest("Missing Stripe-Signature.")
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    except Exception:
        raise BadRequest("Invalid Stripe signature.") from None

    event_type = str(event["type"])
    data_object = event["data"]["object"]
    metadata = data_object.get("metadata") or {}
    reservation_id = metadata.get("reservation_id")
    action = _HANDLED.get(event_type)
    session = _session(request)
    try:
        try:
            await apply_webhook(
                session,
                stripe_event_id=str(event["id"]),
                event_type=event_type,
                reservation_id=reservation_id,
                action=action,
            )
        except IntegrityError:
            logger.info("stripe_event_id=%s outcome=duplicate", event["id"])
    finally:
        await session.close()
    return {"received": True}
