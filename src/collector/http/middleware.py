"""Request/response middleware around HttpClient.request().

Two deques: ``request_middleware`` (FIFO, append) and ``response_middleware``
(LIFO, appendleft — the last registered runs first).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol


class RequestHook(Protocol):
    """Runs before a request is sent; may mutate ``kwargs``."""

    async def __call__(self, method: str, url: str, kwargs: dict[str, Any]) -> None: ...


class ResponseHook(Protocol):
    """Runs after a response is received.

    May return the response unchanged or ``await retry()`` to re-run the request
    (e.g. after solving an anti-bot challenge) and return the new response.
    """

    async def __call__(
        self,
        response: Any,
        *,
        session: Any,
        retry: Callable[[], Awaitable[Any]],
    ) -> Any: ...


class Middleware:
    """Hook container for ``HttpClient``."""

    def __init__(self) -> None:
        self.request_middleware: deque[RequestHook] = deque()
        self.response_middleware: deque[ResponseHook] = deque()

    def request(self, hook: RequestHook) -> RequestHook:
        """Register a request hook (runs in registration order)."""
        self.request_middleware.append(hook)
        return hook

    def response(self, hook: ResponseHook) -> ResponseHook:
        """Register a response hook (last registered runs first)."""
        self.response_middleware.appendleft(hook)
        return hook
