"""HttpClient — a curl_cffi session wrapped in middleware and one retry loop.

``build_http_client`` is here too: assembling the client is knowing what its
pieces are, and that knowledge should not be a second module away from them.
"""

from __future__ import annotations

import asyncio
import email.utils
import inspect
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from collector.http.hooks import Throttle, log_request, log_response
from collector.http.middleware import Middleware
from collector.http.tls import ca_bundle_with_extra_cert
from collector.settings import RetryPolicy, Settings

if TYPE_CHECKING:
    from collector.parser import BaseParser

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
        async def do_request() -> Any:
            # Request hooks run per round trip, not per attempt. A hook that
            # solves a challenge and calls retry() is sending another request to
            # the site, and Throttle has to pace that one too — ``delay`` is a
            # ceiling on what the site sees, not on what the retry policy does.
            for request_hook in self._middleware.request_middleware:
                await request_hook(method, url, kwargs)
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


def build_http_client(parser_cls: type[BaseParser]) -> HttpClient:
    """Assemble an ``HttpClient`` from what ``parser_cls.settings`` declares."""
    settings = parser_cls.settings
    middleware = Middleware()
    middleware.request(log_request)
    if settings.delay or settings.delay_jitter:
        middleware.request(Throttle(settings.delay, settings.delay_jitter))
    for request_hook in settings.request_hooks:
        middleware.request(request_hook)

    middleware.response(log_response)
    for response_hook in settings.response_hooks:
        middleware.response(response_hook)

    session: AsyncSession[Any] = AsyncSession(**session_kwargs(parser_cls, settings))
    return HttpClient(session, middleware, retry=settings.retry)


def session_kwargs(parser_cls: type[BaseParser], settings: Settings) -> dict[str, Any]:
    """Translate settings into ``AsyncSession`` keyword arguments."""
    kwargs: dict[str, Any] = {}
    if settings.impersonate is not None:
        kwargs['impersonate'] = settings.impersonate
    if settings.timeout is not None:
        kwargs['timeout'] = settings.timeout
    if settings.proxy:
        kwargs['proxy'] = settings.proxy
    if settings.headers:
        kwargs['headers'] = dict(settings.headers)

    if settings.extra_ca_cert:
        # Resolved against the file the parser class is defined in, so a site's
        # certificate can live next to the parser that needs it.
        cert_path = Path(inspect.getfile(parser_cls)).parent / settings.extra_ca_cert
        kwargs['verify'] = ca_bundle_with_extra_cert(str(cert_path))
    elif settings.skip_tls_verify:
        kwargs['verify'] = False

    kwargs.update(settings.session_kwargs)
    return kwargs
