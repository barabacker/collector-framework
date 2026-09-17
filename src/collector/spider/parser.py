"""BaseParser — Spider-style base class for parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from typing import Any, ClassVar

from collector.settings import Settings
from collector.spider.context import ParserContext
from collector.spider.request import Request
from collector.spider.response import Response


class BaseParser(ABC):
    """Spider-style parser: what to fetch, and what an item is.

    A subclass sets ``name`` / ``start_urls`` and implements ``parse()`` as an
    async generator: yield a ``Request`` to enqueue it, yield anything else to
    emit it as an item.

    ``settings`` is how a parser declares its own HTTP quirks (proxy, timeout,
    TLS, pacing, limits, hooks) instead of the caller knowing about them; a
    subclass narrows its parent's with ``dataclasses.replace``.

    A parser is declarative and holds no state of its own: the queue, the
    workers, the counters and the failures all belong to
    :class:`~collector.crawler.Crawler`, which is what running one gives back.
    """

    name: ClassVar[str]
    start_urls: ClassVar[list[str]] = []
    settings: ClassVar[Settings] = Settings()

    def __init__(self, ctx: ParserContext) -> None:
        self.ctx = ctx
        self.http = ctx.http

    def request(
        self,
        url: str,
        *,
        method: str = 'GET',
        callback: Callable[[Response], AsyncIterator[Request | Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | str | None = None,
        params: dict[str, Any] | None = None,
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
        crawl's own ``stats.items`` is counted by the crawler and stays accurate
        whether or not an override calls ``super()``.
        """
