"""Where a crawl is assembled, and the bridge to synchronous callers.

Everything a run needs — the HTTP client the crawler declares, the context, the
crawler instance, the crawl around it — is put together in exactly one place:
:func:`open_crawl`. The other three entry points are conveniences over it, so
there is no second copy of the assembly to drift.

    open_crawl()                owns the session for as long as it is used
      └── crawl()               run to completion, async
            └── run_crawler()   the same, for a caller with no event loop
                  └── collect() the same, handing back the items

A crawl is asynchronous, but the thing that starts it usually is not — a CLI, a
cron entry, an RQ task — which is what ``run_crawler`` is for. All four hand back
the :class:`~collector.engine.crawl.Crawl`, which carries the stats, the failures
and the crawler instance itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from collector.crawler.crawler import Crawler, CrawlerContext
from collector.crawler.params import resolve_params
from collector.engine.crawl import Crawl, CrawlError
from collector.engine.params import worker_count
from collector.http.client import build_http_client
from collector.storage.base import Dataset

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_crawl(
    crawler_cls: type[Crawler],
    *,
    params: Mapping[str, Any] | None = None,
    sink: Any | None = None,
    dataset: Dataset | None = None,
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

    A ``dataset``, if given, gets every item the crawl emits; the caller owns it.

    It is also the one place a crawl is assembled, so a failure anywhere under
    it leaves by the same door — wrapped in a :class:`~collector.engine.crawl.CrawlError`
    carrying the crawl, with the original failure chained as its ``__cause__``.
    The one exception is a bad ``params`` value: that is raised as it is, before
    anything — session included — is built.
    """
    # The session's connection pool is sized here rather than inside the
    # builder, because only this side knows the params that can raise the
    # worker count above what the crawler declared.
    params = params or {}
    # Before the session exists: a bad value is a mistake in how the run was
    # asked for, not a crawl that failed, so it costs no connection and is not
    # wrapped in a CrawlError. The crawler resolves them again for itself in
    # __init__, which stays the one place they reach it.
    resolve_params(crawler_cls.params, params)
    http = build_http_client(
        crawler_cls, concurrency=worker_count(params, crawler_cls.settings.concurrency)
    )
    async with http:
        crawler = crawler_cls(CrawlerContext(http=http, params=params, sink=sink, log=log))
        crawl = Crawl(crawler, dataset=dataset)
        try:
            yield crawl
        except Exception as exc:
            raise CrawlError(crawl) from exc
        finally:
            # Before the session closes: a consumer that broke out of stream()
            # may have left the crawl running, and its workers hold that session.
            await crawl.aclose()


async def crawl(
    crawler_cls: type[Crawler],
    *,
    params: Mapping[str, Any] | None = None,
    sink: Any | None = None,
    dataset: Dataset | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> Crawl:
    """Run a crawler to completion and return the crawl that ran it.

    The async entry point: use it when the caller already runs an event loop.
    A failure surfaces as a :class:`~collector.engine.crawl.CrawlError` — ``open_crawl``
    wraps it on the way out, and does it once.
    """
    async with open_crawl(crawler_cls, params=params, sink=sink, dataset=dataset, log=log) as run:
        await run.run()
    return run


def run_crawler(
    crawler_cls: type[Crawler],
    *,
    params: Mapping[str, Any] | None = None,
    sink: Any | None = None,
    dataset: Dataset | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> Crawl:
    """Run a crawler to completion synchronously; return the crawl that ran it.

    ``sink`` is whatever the crawler's ``process_item()`` expects — the framework
    only passes it through. ``log`` defaults to an async wrapper around this
    module's logger.

    Items reach the caller through the crawler: override ``process_item()``, keep
    what you need on ``self``, and read it back off ``crawl.crawler``.
    """
    return asyncio.run(
        crawl(
            crawler_cls,
            params=params,
            sink=sink,
            dataset=dataset,
            log=log or _default_log(crawler_cls),
        )
    )


def collect(
    crawler_cls: type[Crawler],
    *,
    params: Mapping[str, Any] | None = None,
    dataset: Dataset | None = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> list[Any]:
    """Run a crawl and return the items it emitted.

    For a one-off — a script, a notebook, a test — where writing a sink to get
    at the items would be ceremony. A long crawl should still stream into a
    sink rather than pile up in memory.

    It drains ``stream()``, so the crawler's own ``process_item()`` still runs
    and the class handed in runs as itself — no subclass is substituted behind
    the caller's back.
    """

    async def drain() -> list[Any]:
        async with open_crawl(
            crawler_cls, params=params, dataset=dataset, log=log or _default_log(crawler_cls)
        ) as run:
            return [item async for item in run.stream()]

    return asyncio.run(drain())


def _default_log(crawler_cls: type[Crawler]) -> Callable[[str], Awaitable[None]]:
    """Tag a crawler's log lines with its name and send them to this module's logger."""

    async def log(message: str) -> None:
        logger.info('[%s] %s', crawler_cls.name, message)

    return log
