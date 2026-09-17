"""HttpClient — a curl_cffi session wrapped in middleware and one retry loop."""

from __future__ import annotations

import asyncio
import email.utils
import logging
from datetime import UTC, datetime
from typing import Any, cast

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from collector.http.middleware import Middleware
from collector.settings import RetryPolicy

logger = logging.getLogger(__name__)


class HttpClient:
    """HTTP client: each request passes through request/response middleware.

    Retries are one mechanism covering both failure modes — a transport error
    and a retryable status — so a flaky site cannot spend two independent
    attempt budgets. The hook-driven ``retry()`` is separate by design: a hook
    that solves a challenge is not a failed attempt.
    """

    def __init__(
        self,
        session: AsyncSession[Any],
        middleware: Middleware,
        retry: RetryPolicy | None = None,
    ) -> None:
        self._session = session
        self._middleware = middleware
        self._retry = retry or RetryPolicy()

    @property
    def middleware(self) -> Middleware:
        return self._middleware

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retry

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._session.close()

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Run a request through the hooks, retrying per the policy."""
        policy = self._retry
        last_attempt = policy.attempts
        for attempt in range(1, last_attempt + 1):
            try:
                # Hooks mutate kwargs, so each attempt starts from the original.
                response = await self._attempt(method, url, dict(kwargs))
            except RequestException as exc:
                if attempt == last_attempt:
                    raise
                logger.info(
                    'http.retry %s %s attempt=%s/%s error=%r',
                    method,
                    url,
                    attempt,
                    last_attempt,
                    exc,
                )
                await asyncio.sleep(policy.backoff(attempt))
                continue

            if attempt == last_attempt or response.status_code not in policy.statuses:
                return response

            wait = self._wait_for(response, attempt)
            if wait is None:
                # The server asked for longer than we are willing to wait.
                return response
            logger.info(
                'http.retry %s %s attempt=%s/%s status=%s wait=%.1fs',
                method,
                url,
                attempt,
                last_attempt,
                response.status_code,
                wait,
            )
            await asyncio.sleep(wait)

        raise AssertionError('unreachable: the loop returns or raises')  # pragma: no cover

    async def _attempt(self, method: str, url: str, kwargs: dict[str, Any]) -> Any:
        for request_hook in self._middleware.request_middleware:
            await request_hook(method, url, kwargs)

        async def do_request() -> Any:
            return await self._session.request(cast('Any', method), url, **kwargs)

        response = await do_request()

        for response_hook in self._middleware.response_middleware:
            response = await response_hook(response, session=self._session, retry=do_request)

        return response

    def _wait_for(self, response: Any, attempt: int) -> float | None:
        """Seconds to wait before the next attempt; None to stop retrying."""
        policy = self._retry
        if policy.respect_retry_after:
            asked = _retry_after_seconds(response)
            if asked is not None:
                return None if asked > policy.max_retry_after else asked
        return policy.backoff(attempt)


def _retry_after_seconds(response: Any) -> float | None:
    """Read ``Retry-After`` as seconds. Accepts both the delay and date forms."""
    headers = getattr(response, 'headers', None)
    raw = headers.get('Retry-After') if headers is not None else None
    if not raw:
        return None
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)
