"""JamBase JSON → domain models. Fixture is a trimmed live Austin response."""

from datetime import date, time

from app.models import CapacityBand
from app.providers.jambase.mapper import map_event, map_events


def test_maps_live_payload(jambase_payload):
    events = map_events(jambase_payload)
    assert len(events) == 2

    event = events[0]
    assert event.id == "jambase:16114286"
    assert event.title == "ZZ Top at ACL Live at The Moody Theater"
    assert event.start_date == date(2026, 8, 22)
    assert event.start_time == time(20, 0, 0)
    assert event.door_time == time(19, 0, 0)
    assert event.venue.city == "Austin"
    assert event.venue.region == "TX"
    assert event.venue.capacity == 2570
    assert event.venue.capacity_band is CapacityBand.MIDSIZE
    assert event.headliner.name == "ZZ Top"
    assert event.genres == ["blues", "rock"]
    assert [o.seller for o in event.offers if o.is_primary] == ["AXS"]
    assert "price" not in event.offers[0].model_dump()


def test_empty_door_time_is_not_filled_from_showtime(jambase_payload):
    """Live Dirty Heads record: doorTime was "". Showtime stays; door stays missing."""
    dirty = map_events(jambase_payload)[1]
    assert dirty.start_time == time(17, 30, 0)
    assert dirty.door_time is None


def test_missing_fields_stay_none():
    """No invented title, venue name, or times. JamBase "" is missing."""
    event = map_event(
        {
            "identifier": "jambase:4",
            "startDate": "2030-01-01T20:00:00",
            "doorTime": "",
            "image": "",
            "location": {
                "name": "",
                "address": {"streetAddress": "", "addressRegion": "US-TX"},
            },
            "performer": [{"name": "Big Band", "x-isHeadliner": True}],
        }
    )
    assert event.title is None
    assert event.image is None
    assert event.door_time is None
    assert event.start_time == time(20, 0, 0)
    assert event.venue.name is None
    assert event.venue.street_address is None
    assert event.venue.region == "TX"


def test_unusable_or_broken_rows_are_skipped(jambase_payload):
    """Assumption: no identifier or startDate → drop the row, not the search."""
    assert map_event({"name": "No id", "startDate": "2030-01-01"}) is None
    assert map_event({"identifier": "jambase:3", "name": "No date"}) is None
    payload = {"events": [{"garbage": True}, "not-a-dict", *jambase_payload["events"]]}
    assert len(map_events(payload)) == 2
