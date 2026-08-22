"""JamBase implementation of the EventProvider contract."""

from __future__ import annotations

import re

from app.config import Settings
from app.models import Event, EventSearch, GeoPoint, ResolvedLocation
from app.providers.base import EventProvider
from app.providers.jambase.client import JamBaseClient
from app.providers.jambase.mapper import PROVIDER_NAME, map_events

# "34.05,-118.24" -- lets the UI's "use my location" skip geocoding entirely.
LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# "Austin, TX" / "Austin, Texas" -> city + state hint.
US_STATES = {
    "al": "AL", "ak": "AK", "az": "AZ", "ar": "AR", "ca": "CA", "co": "CO",
    "ct": "CT", "de": "DE", "fl": "FL", "ga": "GA", "hi": "HI", "id": "ID",
    "il": "IL", "in": "IN", "ia": "IA", "ks": "KS", "ky": "KY", "la": "LA",
    "me": "ME", "md": "MD", "ma": "MA", "mi": "MI", "mn": "MN", "ms": "MS",
    "mo": "MO", "mt": "MT", "ne": "NE", "nv": "NV", "nh": "NH", "nj": "NJ",
    "nm": "NM", "ny": "NY", "nc": "NC", "nd": "ND", "oh": "OH", "ok": "OK",
    "or": "OR", "pa": "PA", "ri": "RI", "sc": "SC", "sd": "SD", "tn": "TN",
    "tx": "TX", "ut": "UT", "vt": "VT", "va": "VA", "wa": "WA", "wv": "WV",
    "wi": "WI", "wy": "WY", "dc": "DC",
}


class JamBaseProvider(EventProvider):
    name = PROVIDER_NAME
    supports_geocoding = True

    def __init__(self, settings: Settings, client: JamBaseClient | None = None):
        self._settings = settings
        self._client = client or JamBaseClient(
            api_key=settings.jambase_api_key,
            base_url=settings.jambase_base_url,
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
        )

    # -- location ---------------------------------------------------------

    async def find_cities(self, query: str) -> list[ResolvedLocation]:
        """Return every mappable city JamBase sent. Never pick by upcoming count."""
        text = (query or "").strip()
        if not text:
            return []

        if match := LATLNG_RE.match(text):
            lat, lng = float(match.group(1)), float(match.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return [await self._nearest_city(lat, lng)]
            return []

        city_name, state_iso = self._split_city_state(text)
        payload = await self._client.get(
            "/geographies/cities",
            {
                "geoCityName": city_name,
                "geoStateIso": state_iso,
                "cityHasUpcomingEvents": "true",
                "perPage": 10,
            },
        )
        cities = [c for c in (payload.get("cities") or []) if isinstance(c, dict)]
        found: list[ResolvedLocation] = []
        for raw in cities:
            loc = self._city_to_location(raw)
            if loc:
                found.append(loc)
        return found

    async def resolve_location(self, query: str) -> ResolvedLocation | None:
        found = await self.find_cities(query)
        return found[0] if len(found) == 1 else None

    async def _nearest_city(self, lat: float, lng: float) -> ResolvedLocation:
        """Label a raw coordinate with its nearest known city, for display."""
        payload = await self._client.get(
            "/geographies/cities",
            {
                "geoLatitude": lat,
                "geoLongitude": lng,
                "geoRadiusAmount": 50,
                "geoRadiusUnits": "km",
                "cityHasUpcomingEvents": "true",
                "perPage": 5,
            },
        )
        cities = [c for c in (payload.get("cities") or []) if isinstance(c, dict)]
        if cities:
            best = max(cities, key=lambda c: c.get("x-numUpcomingEvents") or 0)
            resolved = self._city_to_location(best)
            if resolved:
                # Keep the user's exact point; borrow only the label.
                return resolved.model_copy(
                    update={"geo": GeoPoint(latitude=lat, longitude=lng)}
                )
        return ResolvedLocation(
            label=f"{lat:.3f}, {lng:.3f}",
            geo=GeoPoint(latitude=lat, longitude=lng),
        )

    @staticmethod
    def _split_city_state(text: str) -> tuple[str, str | None]:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) >= 2 and parts[1]:
            code = US_STATES.get(parts[1].lower().replace(".", ""))
            if code:
                return parts[0], f"US-{code}"
        return parts[0], None

    @staticmethod
    def _city_to_location(city: dict) -> ResolvedLocation | None:
        geo = city.get("geo") or {}
        lat, lng = geo.get("latitude"), geo.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            return None
        address = city.get("address") or {}
        # addressRegion arrives as "US-TX"; show just "TX".
        region_iso = str(address.get("addressRegion") or "")
        region = region_iso.split("-")[-1] if region_iso else None
        name = str(city.get("name") or "").strip() or None
        if not name:
            return None
        upcoming = city.get("x-numUpcomingEvents")
        count = int(upcoming) if isinstance(upcoming, (int, float)) else None
        return ResolvedLocation(
            label=f"{name}, {region}" if region else name,
            geo=GeoPoint(latitude=float(lat), longitude=float(lng)),
            city=name,
            region=region,
            country=str(address.get("addressCountry") or "") or None,
            upcoming_event_count=count,
        )

    # -- events -----------------------------------------------------------

    async def search_events(self, search: EventSearch) -> list[Event]:
        params: dict[str, object] = {
            "geoLatitude": round(search.location.geo.latitude, 4),
            "geoLongitude": round(search.location.geo.longitude, 4),
            "geoRadiusAmount": search.radius_km,
            "geoRadiusUnits": "km",
            "perPage": min(search.limit, self._settings.max_page_size),
            "sort": "eventDate",
            # The lineup is the point of a listing; never suppress it.
            "excludeEventPerformers": "false",
        }
        if search.date_from:
            params["eventDateFrom"] = search.date_from.isoformat()
        if search.date_to:
            params["eventDateTo"] = search.date_to.isoformat()
        if search.genres:
            params["genreSlug"] = "|".join(search.genres)
        if search.query:
            params["name"] = search.query

        payload = await self._client.get("/events", params)
        return map_events(payload)

    async def aclose(self) -> None:
        await self._client.aclose()
