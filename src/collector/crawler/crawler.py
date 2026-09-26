"""Crawler — the declarative class a scraper subclasses — and the context it runs in.

``CrawlerContext`` lives here because it is only ever built beside a crawler and
only ever read through one: it is the crawler's half of a run, where ``Crawl``
owns the other half.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from collector.crawler.params import check_declaration, resolve_params
from collector.crawler.request import Request
from collector.crawler.response import Response
from collector.settings import Settings

if TYPE_CHECKING:
    from collector.engine.crawl import Stats
    from collector.http.client import HttpClient


@dataclass(slots=True)
class CrawlerContext:
    """What a crawler needs to run: HTTP client, params, optional sink and log.

    Four fields, each with a reader: the crawl sends through ``http`` and
    reads its limits out of ``params``, ``Crawler.log()`` writes to ``log``,
    and ``sink`` is the application's own, passed through untouched.

    ``sink`` is deliberately untyped: this framework has no storage contract of
    its own. An application defines what it stores and how, and reads the sink
    back in its own ``process_item()`` override.
    """

    http: HttpClient
    params: Mapping[str, Any] = field(default_factory=dict)
    sink: Any | None = None
    log: Callable[[str], Awaitable[None]] | None = None


class Crawler(ABC):
    """Declarative crawler: what to fetch, and what an item is.

    A subclass sets ``name`` / ``start_urls`` and implements ``parse()`` as an
    async generator: yield a ``Request`` to enqueue it, yield anything else to
    emit it as an item.

    ``settings`` is how a crawler declares its own HTTP quirks (proxy, timeout,
    TLS, pacing, limits, hooks) instead of the caller knowing about them; a
    subclass narrows its parent's with ``dataclasses.replace``.

    ``params`` is how it declares what a run may set — a page limit, a date
    window — typed, and checked before the first request.

    A crawler is declarative and holds no state of its own: the queue, the
    workers, the counters and the failures all belong to
    :class:`~collector.engine.crawl.Crawl`, which is what running one gives back.
    """

    name: ClassVar[str]
    start_urls: ClassVar[list[str]] = []
    settings: ClassVar[Settings] = Settings()
    #: What a run may set, as a frozen dataclass instance holding this
    #: crawler's defaults; a subclass narrows it with ``dataclasses.replace``.
    #: On an instance it is the run's values, typed — a new object, so this
    #: declaration never changes. ``None`` declares nothing and leaves a run's
    #: ``params`` free-form, on ``ctx.params`` only.
    params: Any = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        check_declaration(cls)

    def __init__(self, ctx: CrawlerContext) -> None:
        self.ctx = ctx
        self.http = ctx.http
        self.params = resolve_params(type(self).params, ctx.params)

    def request(
        self,
        url: str,
        *,
        method: str = 'GET',
        callback: Callable[[Response], AsyncIterator[Request | Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | list[tuple[str, str]] | str | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Request:
        """Build a ``Request`` defaulting its callback to ``self.parse``."""
        return Request(
            url=url,
            method=method,
            callback=callback or self.parse,
            metadata=metadata or {},
            headers=headers,
            data=data,
            params=params,
            json=json,
            cookies=cookies,
        )

    async def opened(self) -> None:  # noqa: B027 — optional hook
        """Run once before the crawl starts. A no-op here; override for async setup.

        ``__init__`` cannot ``await`` anything, so this is where a crawler logs
        in or opens a connection pool before ``start_requests()`` runs. If this
        raises, the crawl never starts and ``closed()`` is not called — the
        same rule an ``async with`` block follows when ``__aenter__`` fails.
        """

    async def start_requests(self) -> AsyncIterator[Request]:
        """The requests a crawl begins with. Defaults to ``start_urls``.

        Override it when a crawl starts with something a URL cannot express — a
        POST, a per-start ``metadata``, or a list read at runtime.
        """
        for url in self.start_urls:
            yield self.request(url)

    @abstractmethod
    def parse(self, response: Response) -> AsyncIterator[Request | Any]:
        """yield ``Request`` to enqueue; yield anything else to emit an item."""

    async def log(self, message: str) -> None:
        """Write a message to the job log. No-op if ``ctx.log`` is unset."""
        if self.ctx.log is not None:
            await self.ctx.log(message)

    async def process_item(self, item: Any) -> None:  # noqa: B027 — optional hook
        """Handle one emitted item. A no-op here; override to persist it.

        The framework stores nothing: an application overrides this to write the
        item to ``self.ctx.sink`` and to keep whatever counters it needs. The
        crawl's own ``stats.items`` is counted by the crawl and stays accurate
        whether or not an override calls ``super()``.
        """

    async def on_error(self, request: Request, exc: Exception) -> None:  # noqa: B027 — optional hook
        """Run when a request fails. A no-op here; override to observe it.

        The framework still collects the failure in ``errors`` and re-raises
        the first one at the end whether or not this is overridden — this is
        for reacting (a metric, a note), not for changing what happens next.
        A broken override is logged and otherwise ignored, so it cannot take
        down the worker that called it.
        """

    async def closed(self, stats: Stats) -> None:  # noqa: B027 — optional hook
        """Run once the crawl is over. A no-op here; override to release a resource.

        Always runs — on a clean finish, on ``max_requests``, on a cancelled
        crawl and on one that ends in an error — so a crawler that opened
        something in ``__init__`` has exactly one place to close it, whatever
        ``stats.reason`` turns out to be.
        """
