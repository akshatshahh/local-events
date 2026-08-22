"""Provider-agnostic domain models.

Everything above the provider layer speaks these types only. Adding a new
upstream source means writing a mapper into these models -- no route, service,
or UI code should ever learn a provider's field names.

Optional fields are None when the source did not provide a value. The UI
renders that as "not listed". We do not invent replacement strings.
"""

from __future__ import annotations

from datetime import date, time
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class CapacityBand(StrEnum):
    """Derived venue-size label. Approved interpretation, not a JamBase field.

    Thresholds (assumption, stated): <500 intimate, <2000 small, <10000 midsize,
    <50000 large, else stadium. Unknown when capacity is missing or non-positive.
    """

    INTIMATE = "intimate"
    SMALL = "small"
    MIDSIZE = "midsize"
    LARGE = "large"
    STADIUM = "stadium"
    UNKNOWN = "unknown"

    @classmethod
    def from_capacity(cls, capacity: int | None) -> CapacityBand:
        if capacity is None or capacity <= 0:
            return cls.UNKNOWN
        if capacity < 500:
            return cls.INTIMATE
        if capacity < 2_000:
            return cls.SMALL
        if capacity < 10_000:
            return cls.MIDSIZE
        if capacity < 50_000:
            return cls.LARGE
        return cls.STADIUM


class GeoPoint(BaseModel):
    latitude: float
    longitude: float


class Venue(BaseModel):
    name: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    street_address: str | None = None
    postal_code: str | None = None
    timezone: str | None = None
    geo: GeoPoint | None = None
    capacity: int | None = None
    capacity_band: CapacityBand = CapacityBand.UNKNOWN
    url: str | None = None

    @model_validator(mode="after")
    def _derive_capacity_band(self) -> Venue:
        """Keep the band in lockstep with capacity so no provider can disagree."""
        self.capacity_band = CapacityBand.from_capacity(self.capacity)
        return self

    @property
    def locality(self) -> str | None:
        if self.city and self.region:
            return f"{self.city}, {self.region}"
        return self.city or self.region


class Performer(BaseModel):
    name: str | None = None
    is_headliner: bool = False
    genres: list[str] = Field(default_factory=list)
    image: str | None = None
    url: str | None = None


class TicketOffer(BaseModel):
    seller: str | None = None
    url: str
    is_primary: bool = True


class DecisionSignals(BaseModel):
    """Computed hints. Distance is haversine; tags are capacity bands only."""

    distance_km: float | None = None
    distance_mi: float | None = None
    days_away: int | None = None
    lineup_size: int = 0
    has_tickets: bool = False
    tags: list[str] = Field(default_factory=list)


class Event(BaseModel):
    id: str
    provider: str
    title: str | None = None
    start_date: date
    start_time: time | None = None
    end_date: date | None = None
    door_time: time | None = None
    is_festival: bool = False
    status: str | None = None
    url: str | None = None
    image: str | None = None
    venue: Venue
    performers: list[Performer] = Field(default_factory=list)
    offers: list[TicketOffer] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    signals: DecisionSignals = Field(default_factory=DecisionSignals)

    @property
    def headliner(self) -> Performer | None:
        return next((p for p in self.performers if p.is_headliner), None) or (
            self.performers[0] if self.performers else None
        )


class ResolvedLocation(BaseModel):
    label: str
    geo: GeoPoint
    city: str | None = None
    region: str | None = None
    country: str | None = None


class EventSearch(BaseModel):
    location: ResolvedLocation
    radius_km: float = 40.0
    date_from: date | None = None
    date_to: date | None = None
    genres: list[str] = Field(default_factory=list)
    query: str | None = None
    limit: int = 40


class ProviderStatus(BaseModel):
    name: str
    ok: bool
    event_count: int = 0
    error: str | None = None
    cached: bool = False


class SearchResult(BaseModel):
    location: ResolvedLocation
    events: list[Event]
    providers: list[ProviderStatus]
    genre_facets: dict[str, int] = Field(default_factory=dict)
    total_available: int | None = None
