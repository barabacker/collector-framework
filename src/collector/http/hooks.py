"""What a hook is, and the two the framework ships: logging and throttling.

A hook is a callable, not a registration — the client holds two ordered tuples
of them and calls each in turn. The protocols below are what those tuples are
typed as, and they live beside the hooks so that writing one means reading one
file.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class RequestHook(Protocol):
    """Runs before every round trip; may mutate ``kwargs``.

    Per round trip, not per attempt: a retry — the policy's or a response
    hook's — is another request to the site, and a ``Throttle`` has to pace it.
    """

    async def __call__(self, method: str, url: str, kwargs: dict[str, Any]) -> None: ...


class ResponseHook(Protocol):
    """Runs after a response is received.

    May return the response unchanged, return a different one, or ``await
    retry()`` to re-run the request — after solving an anti-bot challenge, say —
    and return what that produced. A hook's retry spends no attempt budget.
    """

    async def __call__(
        self,
        response: Any,
        *,
        session: Any,
        retry: Callable[[], Awaitable[Any]],
    ) -> Any: ...


async def log_request(method: str, url: str, kwargs: dict[str, Any]) -> None:
    """Request hook: log method and URL."""
    logger.info('http.request %s %s', method, url)


async def log_response(response: Any, *, session: Any, retry: Any) -> Any:
    """Response hook: log url/status/size, return the response unchanged."""
    logger.info(
        'http.response %s status=%s size=%s',
        getattr(response, 'url', '?'),
        response.status_code,
        len(response.content),
    )
    return response


class Throttle:
    """Request hook that keeps a minimum gap between consecutive requests.

    The gap is held behind a lock, so it survives ``concurrency > 1``: workers
    queue up on the hook instead of each pacing itself and firing together.
    ``jitter`` adds a random ``[0, jitter)`` on top, so a crawl does not hit a
    site on a metronome.
    """

    def __init__(self, delay: float, jitter: float = 0.0) -> None:
        self.delay = delay
        self.jitter = jitter
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def __call__(self, method: str, url: str, kwargs: dict[str, Any]) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            gap = self.delay + (random.uniform(0, self.jitter) if self.jitter else 0.0)
            self._next_at = now + gap
