"""Capacity and status changes. Each public function is one transaction."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import CapacityExceeded
from app.reservations.models import (
    EventHold,
    ProcessedWebhookEvent,
    Reservation,
    ReservationStatus,
    utcnow,
)

logger = logging.getLogger(__name__)

FEE_CENTS = 500


def _hold_insert(event_id: str):
    """Insert a zero hold if this event has never been reserved.

    SQLite and Postgres both accept ON CONFLICT DO NOTHING. The dialect is
    chosen from the bind at execution time when we use the generic insert
    below; tests run on SQLite, so the SQLite insert is the one we compile
    when the session is SQLite. Postgres uses the postgresql dialect via
    the session's bind — see `_insert_hold`.
    """
    return sqlite_insert(EventHold).values(event_id=event_id, held_quantity=0)


async def _insert_hold(session: AsyncSession, event_id: str) -> None:
    bind = session.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(EventHold).values(event_id=event_id, held_quantity=0)
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])
    else:
        stmt = _hold_insert(event_id).on_conflict_do_nothing(index_elements=["event_id"])
    await session.execute(stmt)


async def _add_hold(session: AsyncSession, event_id: str, quantity: int, cap: int) -> bool:
    stmt = (
        update(EventHold)
        .where(EventHold.event_id == event_id)
        .where(EventHold.held_quantity + quantity <= cap)
        .values(held_quantity=EventHold.held_quantity + quantity)
        .returning(EventHold.held_quantity)
    )
    result = await session.execute(stmt)
    return result.first() is not None


async def _release_hold(session: AsyncSession, event_id: str, quantity: int) -> None:
    stmt = (
        update(EventHold)
        .where(EventHold.event_id == event_id)
        .where(EventHold.held_quantity >= quantity)
        .values(held_quantity=EventHold.held_quantity - quantity)
    )
    await session.execute(stmt)


async def create_pending(
    session: AsyncSession,
    *,
    event_id: str,
    email: str,
    quantity: int,
    cap: int,
) -> Reservation:
    reservation = Reservation(
        id=str(uuid.uuid4()),
        event_id=event_id,
        email=email,
        quantity=quantity,
        status=ReservationStatus.PENDING.value,
        stripe_session_id=None,
        amount_cents=FEE_CENTS * quantity,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    async with session.begin():
        await _insert_hold(session, event_id)
        if not await _add_hold(session, event_id, quantity, cap):
            raise CapacityExceeded("No spots left for this event.")
        session.add(reservation)
    logger.info("reservation_id=%s outcome=pending", reservation.id)
    return reservation


async def attach_session(
    session: AsyncSession, reservation_id: str, stripe_session_id: str
) -> None:
    async with session.begin():
        reservation = await session.get(Reservation, reservation_id)
        if reservation is None or reservation.status != ReservationStatus.PENDING.value:
            return
        reservation.stripe_session_id = stripe_session_id
        reservation.updated_at = utcnow()
    logger.info("reservation_id=%s outcome=session_saved", reservation_id)


async def cancel_pending(session: AsyncSession, reservation_id: str) -> None:
    """Release a hold when Checkout never opened. Confirmed rows stay put."""
    async with session.begin():
        reservation = await session.get(Reservation, reservation_id)
        if reservation is None or reservation.status != ReservationStatus.PENDING.value:
            return
        reservation.status = ReservationStatus.CANCELED.value
        reservation.updated_at = utcnow()
        await _release_hold(session, reservation.event_id, reservation.quantity)
    logger.info("reservation_id=%s outcome=canceled", reservation_id)


async def apply_webhook(
    session: AsyncSession,
    *,
    stripe_event_id: str,
    event_type: str,
    reservation_id: str | None,
    action: str | None,
) -> str:
    """Record the Stripe event id, then move the reservation if the transition is legal.

    A duplicate stripe_event_id raises IntegrityError from the unique primary
    key so the caller can return 200 without applying the transition twice.
    """
    async with session.begin():
        session.add(
            ProcessedWebhookEvent(
                stripe_event_id=stripe_event_id,
                type=event_type,
                processed_at=utcnow(),
            )
        )
        await session.flush()
        outcome = await _transition(session, reservation_id, action)
    logger.info(
        "stripe_event_id=%s reservation_id=%s outcome=%s",
        stripe_event_id,
        reservation_id or "",
        outcome,
    )
    return outcome


async def _transition(
    session: AsyncSession, reservation_id: str | None, action: str | None
) -> str:
    if action is None or not reservation_id:
        return "ignored"
    reservation = await session.get(Reservation, reservation_id)
    if reservation is None:
        return "missing"
    if reservation.status != ReservationStatus.PENDING.value:
        # expired-after-completed must not undo a payment, or free the spot.
        return "ignored"
    if action == "confirm":
        reservation.status = ReservationStatus.CONFIRMED.value
        reservation.updated_at = utcnow()
        return "confirmed"
    if action == "expire":
        reservation.status = ReservationStatus.EXPIRED.value
        reservation.updated_at = utcnow()
        await _release_hold(session, reservation.event_id, reservation.quantity)
        return "expired"
    return "ignored"
