"""Where a crawl is assembled, and the bridge to synchronous callers.

Everything a run needs — the HTTP client the parser declares, the context, the
parser instance, the crawler around it — is put together in exactly one place:
:func:`open_crawler`. The other three entry points are conveniences over it, so
there is no second copy of the assembly to drift.

    open_crawler()              owns the session for as long as it is used
      └── crawl()               run to completion, async
            └── run_parser()    the same, for a caller with no event loop
                  └── collect() the same, handing back the items

A crawl is asynchronous, but the thing that starts it usually is not — a CLI, a
cron entry, an RQ task — which is what ``run_parser`` is for. All four hand back
the :class:`~collector.engine.crawler.Crawler`, which carries the stats, the failures
and the parser instance itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from collector.engine.crawler import Crawler
from collector.http.client import build_http_client
from collector.spider.parser import Parser, ParserContext

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_crawler(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
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

    It is also the one place a crawl is assembled, so a failure anywhere under
    it leaves by the same door — with the crawler attached to the exception.
    """
    http = build_http_client(parser_cls)
    async with http:
        crawler = Crawler(
            parser_cls(ParserContext(http=http, params=params or {}, sink=sink, log=log))
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


async def crawl(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> Crawler:
    """Run a parser to completion and return the crawler that ran it.

    The async entry point: use it when the caller already runs an event loop.
    A failure propagates with the crawler attached — ``open_crawler`` does that
    on the way out, and does it once.
    """
    async with open_crawler(parser_cls, params=params, sink=sink, log=log) as crawler:
        await crawler.run()
    return crawler


def run_parser(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> Crawler:
    """Run a parser to completion synchronously; return the crawler that ran it.

    ``sink`` is whatever the parser's ``process_item()`` expects — the framework
    only passes it through. ``log`` defaults to an async wrapper around this
    module's logger.

    Items reach the caller through the parser: override ``process_item()``, keep
    what you need on ``self``, and read it back off ``crawler.parser``.
    """
    return asyncio.run(
        crawl(parser_cls, params=params, sink=sink, log=log or _default_log(parser_cls))
    )


def collect(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> list[Any]:
    """Run a crawl and return the items it emitted.

    For a one-off — a script, a notebook, a test — where writing a sink to get
    at the items would be ceremony. A long crawl should still stream into a
    sink rather than pile up in memory.

    It drains ``stream()``, so the parser's own ``process_item()`` still runs
    and the class handed in runs as itself — no subclass is substituted behind
    the caller's back.
    """

    async def drain() -> list[Any]:
        async with open_crawler(
            parser_cls, params=params, log=log or _default_log(parser_cls)
        ) as crawler:
            return [item async for item in crawler.stream()]

    return asyncio.run(drain())


def _default_log(parser_cls: type[Parser]) -> Callable[[str], Awaitable[None]]:
    """Tag a parser's log lines with its name and send them to this module's logger."""

    async def log(message: str) -> None:
        logger.info('[%s] %s', parser_cls.name, message)

    return log


def _attach_crawler(exc: BaseException, crawler: Crawler) -> None:
    """Make a failed crawl's crawler reachable from the exception it raised.

    ``run()`` re-raises the first failure only, and raising drops the crawler
    with the frame that held it — so a crawl that survived twenty bad pages
    could report one and lose the other nineteen. They ride out on the
    exception instead, as ``exc.crawler.errors``.

    Called from ``open_crawler`` alone. Calling it twice on its way up a nested
    stack of entry points would note the same failure count twice.
    """
    with contextlib.suppress(AttributeError):
        # An exception with __slots__ and no __dict__ cannot carry the crawler.
        # Losing it is bad; masking the error the caller came for is worse.
        exc.crawler = crawler  # type: ignore[attr-defined]
    if len(crawler.errors) > 1:
        exc.add_note(f'{len(crawler.errors)} requests failed in this crawl; see exc.crawler.errors')
