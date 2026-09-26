"""Running many crawlers at once: a cap, a name on every log line, and each
crawler's failure kept to itself.

An application crawling several sites needs the same few things around
``open_crawl()`` every time — how many run at once, which crawler a log line
came from, one site failing without stopping the rest, and a result per crawler
as it finishes. What happens to each crawler's items stays the application's:
``consume`` gets the open crawl, and without it the crawler's own
``process_item()`` sees them.

    async for outcome in crawl_many(crawlers, concurrency=16, consume=consume):
        print(outcome.crawler_cls.name, outcome.error or outcome.crawl.stats)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from collector.crawler.crawler import Crawler
from collector.crawler.params import resolve_params
from collector.engine.crawl import Crawl, CrawlError
from collector.engine.runner import open_crawl
from collector.storage.base import Dataset

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Outcome:
    """How one crawler's run ended."""

    crawler_cls: type[Crawler]
    #: The crawl that ran, with its stats and errors — kept on a failure too, so
    #: a site that fell over after two thousand items still says so. ``None``
    #: only when it failed before a crawl existed (building the HTTP client).
    crawl: Crawl | None
    #: ``None`` on success; otherwise the original failure, not the
    #: ``CrawlError`` that carried it out of ``open_crawl()``.
    error: Exception | None
    #: Seconds from this crawler's start — after waiting for a slot — to its end.
    elapsed: float


async def crawl_many(
    crawlers: Iterable[type[Crawler]],
    *,
    concurrency: int | None = None,
    params: Mapping[str, Any] | None = None,
    dataset: Dataset | None = None,
    consume: Callable[[Crawl], Awaitable[None]] | None = None,
    log: Callable[[str, str], Awaitable[None]] | None = None,
) -> AsyncIterator[Outcome]:
    """Run ``crawlers`` at once and yield an ``Outcome`` as each one finishes.

    At most ``concurrency`` run together (``None``: no cap). The same
    ``params`` go to every crawler, and are checked for all of them before any
    starts: a value one crawler refuses stops the run while nothing has been
    built. One ``dataset``, if given, takes every crawler's items, each under
    its name. ``consume(crawl)`` handles a crawler's items — typically by
    iterating ``crawl.stream()`` — and without it the crawl just runs.
    ``log(name, message)`` receives every crawler's log lines with its name.

    A caller that stops early wraps this in ``contextlib.aclosing()``: closing
    the generator is what cancels the crawlers still running, and Python closes
    an abandoned one only when it gets around to finalising it.
    """
    classes = list(crawlers)
    params = params or {}
    if concurrency is not None and concurrency < 1:
        raise ValueError(f'concurrency must be at least 1, got {concurrency}')
    for crawler_cls in classes:
        try:
            resolve_params(crawler_cls.params, params)
        except ValueError as exc:
            raise ValueError(f'{crawler_cls.__name__}: {exc}') from exc

    gate = asyncio.Semaphore(concurrency) if concurrency is not None else None
    tasks = [
        asyncio.create_task(_run_one(crawler_cls, gate, params, dataset, consume, log))
        for crawler_cls in classes
    ]
    try:
        for finished in asyncio.as_completed(tasks):
            yield await finished
    finally:
        # Reached when the caller stops early, too: crawlers left running would
        # outlive the loop that is about to stop listening to them.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_one(
    crawler_cls: type[Crawler],
    gate: asyncio.Semaphore | None,
    params: Mapping[str, Any],
    dataset: Dataset | None,
    consume: Callable[[Crawl], Awaitable[None]] | None,
    log: Callable[[str, str], Awaitable[None]] | None,
) -> Outcome:
    """One crawler from slot to outcome. Any ``Exception`` ends up in the outcome."""
    async with gate if gate is not None else contextlib.nullcontext():
        started = time.monotonic()
        crawl: Crawl | None = None
        error: Exception | None = None
        try:
            async with open_crawl(
                crawler_cls, params=params, dataset=dataset, log=_tagged(crawler_cls, log)
            ) as crawl:
                if consume is not None:
                    await consume(crawl)
                else:
                    await crawl.run()
        except CrawlError as exc:
            crawl = exc.crawl
            error = exc.__cause__ if isinstance(exc.__cause__, Exception) else exc
        except Exception as exc:  # noqa: BLE001 — one crawler must not stop the others
            # Raised outside the crawl: building the client, or closing it. In
            # the second case the crawl ran and ``crawl`` still holds it, stats
            # and all; in the first it was never set.
            error = exc
        return Outcome(crawler_cls, crawl, error, time.monotonic() - started)


def _tagged(
    crawler_cls: type[Crawler], log: Callable[[str, str], Awaitable[None]] | None
) -> Callable[[str], Awaitable[None]]:
    """The one-argument log a crawl expects, naming the crawler it came from."""
    name = getattr(crawler_cls, 'name', crawler_cls.__name__)

    async def tagged(message: str) -> None:
        if log is None:
            logger.info('[%s] %s', name, message)
        else:
            await log(name, message)

    return tagged
