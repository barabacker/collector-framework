"""Where a crawl is assembled, and the bridge to synchronous callers.

Everything a run needs — the HTTP client the parser declares, the context, the
parser instance, the crawl around it — is put together in exactly one place:
:func:`open_crawl`. The other three entry points are conveniences over it, so
there is no second copy of the assembly to drift.

    open_crawl()                owns the session for as long as it is used
      └── crawl()               run to completion, async
            └── run_parser()    the same, for a caller with no event loop
                  └── collect() the same, handing back the items

A crawl is asynchronous, but the thing that starts it usually is not — a CLI, a
cron entry, an RQ task — which is what ``run_parser`` is for. All four hand back
the :class:`~collector.engine.crawl.Crawl`, which carries the stats, the failures
and the parser instance itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from collector.engine.crawl import Crawl
from collector.engine.params import worker_count
from collector.http.client import build_http_client
from collector.spider.parser import Parser, ParserContext

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_crawl(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> AsyncIterator[Crawl]:
    """Build a crawl and keep its HTTP session open for as long as it is used.

    What ``stream()`` needs: the session has to outlive the iteration, and a
    consumer that stops early has to close it deterministically. A context
    manager does that where an async generator wrapping ``async with`` would
    leave it to whenever the generator happened to be finalised.

        async with open_crawl(Quotes) as crawl:
            async for quote in crawl.stream():
                await save(quote)
            print(crawl.stats)

    It is also the one place a crawl is assembled, so a failure anywhere under
    it leaves by the same door — with the crawl attached to the exception.
    """
    # The session's connection pool is sized here rather than inside the
    # builder, because only this side knows the params that can raise the
    # worker count above what the parser declared.
    params = params or {}
    http = build_http_client(
        parser_cls, concurrency=worker_count(params, parser_cls.settings.concurrency)
    )
    async with http:
        crawl = Crawl(parser_cls(ParserContext(http=http, params=params, sink=sink, log=log)))
        try:
            yield crawl
        except Exception as exc:
            _attach_crawl(exc, crawl)
            raise
        finally:
            # Before the session closes: a consumer that broke out of stream()
            # may have left the crawl running, and its workers hold that session.
            await crawl.aclose()


async def crawl(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> Crawl:
    """Run a parser to completion and return the crawl that ran it.

    The async entry point: use it when the caller already runs an event loop.
    A failure propagates with the crawl attached — ``open_crawl`` does that
    on the way out, and does it once.
    """
    async with open_crawl(parser_cls, params=params, sink=sink, log=log) as run:
        await run.run()
    return run


def run_parser(
    parser_cls: type[Parser],
    *,
    params: dict[str, str] | None = None,
    sink: Any | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> Crawl:
    """Run a parser to completion synchronously; return the crawl that ran it.

    ``sink`` is whatever the parser's ``process_item()`` expects — the framework
    only passes it through. ``log`` defaults to an async wrapper around this
    module's logger.

    Items reach the caller through the parser: override ``process_item()``, keep
    what you need on ``self``, and read it back off ``crawl.crawler``.
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
        async with open_crawl(
            parser_cls, params=params, log=log or _default_log(parser_cls)
        ) as run:
            return [item async for item in run.stream()]

    return asyncio.run(drain())


def _default_log(parser_cls: type[Parser]) -> Callable[[str], Awaitable[None]]:
    """Tag a parser's log lines with its name and send them to this module's logger."""

    async def log(message: str) -> None:
        logger.info('[%s] %s', parser_cls.name, message)

    return log


def _attach_crawl(exc: BaseException, crawl: Crawl) -> None:
    """Make a failed run's ``Crawl`` reachable from the exception it raised.

    ``run()`` re-raises the first failure only, and raising drops the crawl
    with the frame that held it — so a crawl that survived twenty bad pages
    could report one and lose the other nineteen. They ride out on the
    exception instead, as ``exc.crawl.errors``.

    Called from ``open_crawl`` alone. Calling it twice on its way up a nested
    stack of entry points would note the same failure count twice.
    """
    with contextlib.suppress(AttributeError):
        # An exception with __slots__ and no __dict__ cannot carry the crawl.
        # Losing it is bad; masking the error the caller came for is worse.
        exc.crawl = crawl  # type: ignore[attr-defined]
    if len(crawl.errors) > 1:
        exc.add_note(f'{len(crawl.errors)} requests failed in this crawl; see exc.crawl.errors')
