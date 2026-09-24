"""Retry schedule shared with the JamBase client.

Retry network failures, 429, and 5xx. A 4xx will not change on its own, so it
is raised immediately. Delays match JamBaseClient: 250ms, then 500ms.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def call_with_backoff[T](
    fn: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[BaseException], bool],
    max_retries: int = 2,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    pause = sleep or asyncio.sleep
    last: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:
            last = exc
            if not is_retryable(exc) or attempt >= max_retries:
                raise
            delay = 0.25 * (2**attempt)
            logger.warning("retryable failure (attempt %d): %s", attempt + 1, type(exc).__name__)
            await pause(delay)
    assert last is not None
    raise last
