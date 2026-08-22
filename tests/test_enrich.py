"""Only the derived signals we actually ship: distance and capacity bands."""

from app.models import GeoPoint
from app.services.enrich import enrich
from tests.factories import make_event

AUSTIN = GeoPoint(latitude=30.30, longitude=-97.75)


def test_distance_is_computed_or_left_missing():
    located = enrich(make_event(lat=30.40, lng=-97.75), AUSTIN)
    assert located.signals.distance_km is not None
    missing = enrich(make_event(lat=None), AUSTIN)
    assert missing.signals.distance_km is None


def test_capacity_band_is_labeled_only_when_capacity_exists():
    assert "Intimate venue" in enrich(make_event(capacity=200), AUSTIN).signals.tags
    tags = enrich(make_event(capacity=None), AUSTIN).signals.tags
    assert not any(t in tags for t in ("Intimate venue", "Small venue", "Midsize venue", "Arena", "Stadium"))
