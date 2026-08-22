"""Small builders so tests state only what they care about."""

from datetime import date, time, timedelta

from app.models import Event, GeoPoint, Performer, TicketOffer, Venue


def make_event(
    *,
    id: str = "jambase:1",
    provider: str = "jambase",
    title: str | None = "Show",
    start: date | None = None,
    start_time: time | None = None,
    city: str | None = "Austin",
    venue: str | None = "The Mohawk",
    lat: float | None = 30.30,
    lng: float | None = -97.75,
    capacity: int | None = None,
    performers: list[Performer] | None = None,
    offers: list[TicketOffer] | None = None,
    image: str | None = None,
    timezone: str | None = "America/Chicago",
) -> Event:
    return Event(
        id=id,
        provider=provider,
        title=title,
        start_date=start or date.today() + timedelta(days=3),
        start_time=start_time,
        image=image,
        venue=Venue(
            name=venue,
            city=city,
            region="TX",
            timezone=timezone,
            capacity=capacity,
            geo=GeoPoint(latitude=lat, longitude=lng) if lat is not None else None,
        ),
        performers=performers or [],
        offers=offers or [],
    )


def headliner(name: str = "Act") -> Performer:
    return Performer(name=name, is_headliner=True)
