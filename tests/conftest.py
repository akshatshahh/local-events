import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def jambase_payload() -> dict:
    """Trimmed live JamBase /events response (Austin, fetched 2026-08-22)."""
    return json.loads((FIXTURES / "jambase_events.json").read_text())


@pytest.fixture
def jambase_cities() -> dict:
    """Trimmed live /geographies/cities response for Austin, TX."""
    return json.loads((FIXTURES / "jambase_cities.json").read_text())
