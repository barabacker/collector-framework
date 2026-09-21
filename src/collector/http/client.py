"""HttpClient — a curl_cffi session wrapped in hooks and one retry loop.

``build_http_client`` is here too: assembling the client is knowing what its
pieces are, and that knowledge should not be a second module away from them.
"""

from __future__ import annotations

import asyncio
import email.utils
import inspect
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from collector.http.hooks import (
    RequestHook,
    ResponseHook,
    Throttle,
    log_request,
    log_response,
)
from collector.http.tls import ca_bundle_with_extra_cert
from collector.settings import RetryPolicy, Settings

if TYPE_CHECKING:
    from collector.spider.parser import Parser

logger = logging.getLogger(__name__)


class HttpClient:
    """HTTP client: every request passes through the hooks, in the order given.

    The hooks are two ordered tuples rather than a container that registers
    them, because order is the only thing a container was deciding and a tuple
    says it outright: ``request_hooks`` run front to back before each round
    trip, ``response_hooks`` front to back after it.

    Retries are one mechanism covering both failure modes — a transport error
    and a retryable status — so a flaky site cannot spend two independent
    attempt budgets. The hook-driven ``retry()`` is separate by design: a hook
    that solves a challenge is not a failed attempt.
    """

    def __init__(
        self,
        session: AsyncSession[Any],
        *,
        request_hooks: Sequence[RequestHook] = (),
        response_hooks: Sequence[ResponseHook] = (),
        retry: RetryPolicy | None = None,
    ) -> None:
        self._session = session
        self._request_hooks = tuple(request_hooks)
        self._response_hooks = tuple(response_hooks)
        self._retry = retry or RetryPolicy()

    @property
    def request_hooks(self) -> tuple[RequestHook, ...]:
        return self._request_hooks

    @property
    def response_hooks(self) -> tuple[ResponseHook, ...]:
        return self._response_hooks

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
            for request_hook in self._request_hooks:
                await request_hook(method, url, kwargs)
            return await self._session.request(cast('Any', method), url, **kwargs)

        response = await do_request()

        for response_hook in self._response_hooks:
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


def build_http_client(parser_cls: type[Parser], *, concurrency: int | None = None) -> HttpClient:
    """Assemble an ``HttpClient`` from what ``parser_cls.settings`` declares.

    The two orders are the whole of it, and both read as written: log the
    request first, then pace it, then let the parser's own hooks have it; and on
    the way back the parser's hooks first, with logging last so that it reports
    the response actually returned.

    ``concurrency`` is how many workers the crawl will really run, which the
    params can raise above what ``settings`` declares — so the caller that
    knows both works it out and passes it, and the session is sized to match.
    Left out, the declared value stands.
    """
    settings = parser_cls.settings
    workers = max(settings.concurrency if concurrency is None else concurrency, 1)
    request_hooks: list[RequestHook] = [log_request]
    if settings.delay or settings.delay_jitter:
        request_hooks.append(Throttle(settings.delay, settings.delay_jitter))
    request_hooks.extend(settings.request_hooks)

    session: AsyncSession[Any] = AsyncSession(**session_kwargs(parser_cls, settings, workers))
    return HttpClient(
        session,
        request_hooks=tuple(request_hooks),
        response_hooks=(*settings.response_hooks, log_response),
        retry=settings.retry,
    )


def session_kwargs(
    parser_cls: type[Parser], settings: Settings, workers: int = 1
) -> dict[str, Any]:
    """Translate settings into ``AsyncSession`` keyword arguments."""
    # One curl client per worker. The session defaults to ten of them and queues
    # the rest, so without this a crawl declaring more workers than that got
    # them, and they waited on the pool instead of on the site — `concurrency`
    # silently stopped meaning anything past ten.
    kwargs: dict[str, Any] = {'max_clients': workers}
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
