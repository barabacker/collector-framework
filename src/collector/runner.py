"""Bridge between the async crawl engine and synchronous callers.

A crawl is asynchronous, but the thing that starts it usually is not — a CLI, a
cron entry, an RQ task. ``run_parser`` builds the HTTP client the parser
declares, runs the crawl under ``asyncio.run`` and hands back the
:class:`~collector.crawler.Crawler`, which carries the stats, the failures and
the parser instance itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from collector.crawler import Crawler
from collector.http.factory import build_http_client
from collector.spider import BaseParser, ParserContext

logger = logging.getLogger(__name__)


async def crawl(
    parser_cls: type[BaseParser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
    on_item: Callable[[Any], None] | None = None,
) -> Crawler:
    """Run a parser to completion and return the crawler that ran it.

    The async entry point: use it when the caller already runs an event loop.
    """
    http = build_http_client(parser_cls)
    async with http:
        ctx = ParserContext(http=http, params=params or {}, sink=sink, log=log)
        crawler = Crawler(parser_cls(ctx), on_item=on_item)
        try:
            await crawler.run()
        except Exception as exc:
            _attach_crawler(exc, crawler)
            raise
    return crawler


def _attach_crawler(exc: BaseException, crawler: Crawler) -> None:
    """Make a failed crawl's crawler reachable from the exception it raised.

    ``run()`` re-raises the first failure only, and raising drops the crawler
    with the frame that held it — so a crawl that survived twenty bad pages
    could report one and lose the other nineteen. They ride out on the
    exception instead, as ``exc.crawler.errors``.
    """
    with contextlib.suppress(AttributeError):
        # An exception with __slots__ and no __dict__ cannot carry the crawler.
        # Losing it is bad; masking the error the caller came for is worse.
        exc.crawler = crawler  # type: ignore[attr-defined]
    if len(crawler.errors) > 1:
        exc.add_note(f'{len(crawler.errors)} requests failed in this crawl; see exc.crawler.errors')


@asynccontextmanager
async def open_crawler(
    parser_cls: type[BaseParser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
    on_item: Callable[[Any], None] | None = None,
) -> AsyncIterator[Crawler]:
    """Build a crawler and keep its HTTP session open for as long as it is used.

    What ``stream()`` needs: the session has to outlive the iteration, and a
    consumer that stops early has to close it deterministically. A context
    manager does that where an async generator wrapping ``async with`` would
    leave it to whenever the generator happened to be finalised.

        async with open_crawler(Quotes) as crawler:
            async for quote in crawler.stream():
                await save(quote)
            print(crawler.stats)
    """
    http = build_http_client(parser_cls)
    async with http:
        crawler = Crawler(
            parser_cls(ParserContext(http=http, params=params or {}, sink=sink, log=log)),
            on_item=on_item,
        )
        try:
            yield crawler
        except Exception as exc:
            _attach_crawler(exc, crawler)
            raise
        finally:
            # Before the session closes: a consumer that broke out of stream()
            # may have left the crawl running, and its workers hold that session.
            await crawler.aclose()


def collect(
    parser_cls: type[BaseParser],
    *,
    params: dict[str, str] | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> list[Any]:
    """Run a crawl and return the items it emitted.

    For a one-off — a script, a notebook, a test — where writing a sink to get
    at the items would be ceremony. A long crawl should still stream into a
    sink rather than pile up in memory.
    """
    items: list[Any] = []
    run_parser(parser_cls, params=params, log=log, on_item=items.append)
    return items


def run_parser(
    parser_cls: type[BaseParser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
    on_item: Callable[[Any], None] | None = None,
) -> Crawler:
    """Run a parser to completion synchronously; return the crawler that ran it.

    ``sink`` is whatever the parser's ``process_item()`` expects — the framework
    only passes it through. ``on_item`` is the caller's own item handler, run
    after ``process_item()``. ``log`` defaults to an async wrapper around this
    module's logger.
    """
    if log is None:

        async def log(message: str) -> None:  # noqa: A001 — same name by design
            logger.info('[%s] %s', parser_cls.name, message)

    return asyncio.run(crawl(parser_cls, params=params, sink=sink, log=log, on_item=on_item))
