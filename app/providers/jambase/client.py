"""Thin async HTTP client for the JamBase v3 API.

Owns transport concerns only -- auth, timeouts, retries, error translation.
Response shaping lives in mapper.py so the two can be tested independently.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.errors import ProviderAuthError, ProviderUnavailable

logger = logging.getLogger(__name__)

# Transient conditions worth a retry. 429 included: JamBase rate-limits trial keys.
RETRY_STATUS = {429, 500, 502, 503, 504}


class JamBaseClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "local-events-app/1.0",
            },
        )

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise ProviderAuthError("JAMBASE_API_KEY is not configured.")

        # Drop empty values so we never send `&genreSlug=` and confuse the API.
        clean = {k: v for k, v in params.items() if v not in (None, "", [])}

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.get(path, params=clean)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("jambase timeout on %s (attempt %d)", path, attempt + 1)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("jambase transport error on %s: %s", path, exc)
            else:
                if response.status_code in (401, 403):
                    # Never retry a rejected key, and never echo the key itself.
                    raise ProviderAuthError("JamBase rejected the configured API key.")
                if response.status_code == 404:
                    return {}
                if response.status_code in RETRY_STATUS:
                    last_error = ProviderUnavailable(
                        f"JamBase returned {response.status_code}."
                    )
                    logger.warning(
                        "jambase %s on %s (attempt %d)",
                        response.status_code,
                        path,
                        attempt + 1,
                    )
                elif response.is_error:
                    # 4xx: our query is wrong. Retrying will not help.
                    raise ProviderUnavailable(
                        f"JamBase rejected the request ({response.status_code})."
                    )
                else:
                    return response.json()

            if attempt < self._max_retries:
                await asyncio.sleep(0.25 * (2**attempt))  # 250ms, 500ms

        raise ProviderUnavailable(f"JamBase is unreachable: {last_error}")

    async def aclose(self) -> None:
        await self._client.aclose()
