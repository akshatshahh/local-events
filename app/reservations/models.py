"""Reservation rows. Pending and confirmed both consume capacity."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ReservationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELED = "canceled"


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(512), index=True)
    email: Mapped[str] = mapped_column(String(320))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)
    stripe_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessedWebhookEvent(Base):
    __tablename__ = "processed_webhook_events"

    stripe_event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(80))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventHold(Base):
    """Running count of spots that are pending or confirmed.

    One UPDATE ... WHERE held + qty <= cap is the capacity check. Counting
    rows in a separate statement would let two transactions both pass.
    """

    __tablename__ = "event_holds"

    event_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    held_quantity: Mapped[int] = mapped_column(Integer, default=0)
