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
from collections.abc import Awaitable, Callable, Collection
from typing import Any, Protocol

from collector.settings import DEFAULT_RETRY_STATUSES

logger = logging.getLogger(__name__)


class RequestHook(Protocol):
    """Runs before every round trip; may mutate ``kwargs``.

    Per round trip, not per attempt: a retry — the policy's or a response
    hook's — is another request to the site, and a ``Throttle`` has to pace it.

    Typed by the awaitable it hands back rather than declared ``async def``,
    which would describe an implementation. All the client needs is something
    to await, so a hook factory, a ``functools.partial`` around a coroutine or
    an object with ``__await__`` qualifies as squarely as a plain ``async def``.
    """

    def __call__(self, method: str, url: str, kwargs: dict[str, Any]) -> Awaitable[None]: ...


class ResponseHook(Protocol):
    """Runs after a response is received.

    May return the response unchanged, return a different one, or ``await
    retry()`` to re-run the request — after solving an anti-bot challenge, say —
    and return what that produced. A hook's retry spends no attempt budget.

    Typed by the awaitable it hands back, for the reason ``RequestHook`` gives:
    what the client awaits is the point, and being a coroutine function is one
    way to provide it rather than the requirement.
    """

    def __call__(
        self,
        response: Any,
        *,
        session: Any,
        retry: Callable[[], Awaitable[Any]],
    ) -> Awaitable[Any]: ...


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


class AutoThrottle:
    """Response hook: widen a ``Throttle``'s delay on a retryable status, narrow it back otherwise.

    Wraps an existing ``Throttle`` rather than pacing on its own — ``delay`` is
    a plain attribute, and this is the only thing that ever writes to it after
    construction. The floor is whatever the ``Throttle`` was declared with;
    widening multiplies by ``factor`` up to ``ceiling``, narrowing divides back
    down, never below that floor.
    """

    def __init__(
        self,
        throttle: Throttle,
        *,
        ceiling: float,
        factor: float = 2.0,
        statuses: Collection[int] = DEFAULT_RETRY_STATUSES,
    ) -> None:
        self._throttle = throttle
        self._floor = throttle.delay
        self._ceiling = ceiling
        self._factor = factor
        self._statuses = statuses

    async def __call__(self, response: Any, *, session: Any, retry: Any) -> Any:
        if response.status_code in self._statuses:
            self._throttle.delay = min(self._throttle.delay * self._factor, self._ceiling)
        else:
            self._throttle.delay = max(self._throttle.delay / self._factor, self._floor)
        return response
