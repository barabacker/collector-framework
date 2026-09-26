"""Crawl — the engine that runs a crawler: queue, workers, limits, stats.

Split out of ``Crawler`` so that a crawler stays declarative. A crawler says
*what* to fetch and what to do with an item; the crawl owns everything about
one run — the queue, the workers, the counters and the failures — and is what
the caller gets back when the run is over.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from collector.crawler.request import Request, request_key
from collector.crawler.response import Response
from collector.engine.params import read_max_errors, read_max_requests, worker_count

if TYPE_CHECKING:
    from collector.crawler.crawler import Crawler
    from collector.storage.base import Dataset

logger = logging.getLogger(__name__)

#: Distinguishes "no item" from an item that happens to be ``None``.
_MISSING = object()


@dataclass(slots=True)
class Stats:
    """What one crawl did. Times are ``time.monotonic()``, so only gaps mean anything."""

    #: Requests taken off the queue and sent. Retries happen inside the HTTP
    #: client and are invisible here, so this counts *requests the crawler
    #: asked for*, not round trips curl made.
    requests: int = 0
    errors: int = 0
    items: int = 0
    #: Requests dropped because this crawl had already queued one with the same
    #: key. Never sent, so not in ``requests``.
    duplicates: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    #: Why the crawl ended: ``'done'`` (the queue drained), ``'max_requests'``
    #: (the ceiling was reached), ``'max_errors'`` (more requests failed than
    #: it tolerates) or ``'cancelled'`` (something stopped it — a
    #: consumer that broke out of ``stream()``, or a cancel from outside).
    reason: str = 'done'

    @property
    def elapsed(self) -> float:
        """Seconds the crawl has been running, or ran for once finished."""
        end = time.monotonic() if self.finished_at is None else self.finished_at
        return end - self.started_at


class CrawlError(Exception):
    """A crawl failed. The original failure is chained as ``__cause__``.

    ``crawl`` carries the stats and every failure the crawl collected, not
    only the one that ends up here — a crawl stopped after twenty failures
    reports one on ``__cause__`` and all twenty on ``crawl.errors``.
    """

    def __init__(self, crawl: Crawl) -> None:
        count = len(crawl.errors)
        super().__init__(
            f'{count} requests failed in this crawl; see .crawl.errors'
            if count > 1
            else 'a request failed in this crawl; see .crawl for the request and its stats'
        )
        self.crawl = crawl


@dataclass(slots=True)
class Crawl:
    """Runs one crawler to completion and holds everything that run produced.

    An item reaches its consumer three ways: the crawler's own
    ``process_item()`` pushes it, a ``dataset`` given to the run stores it, and
    ``stream()`` pulls it. A caller who wants the items without writing an async
    loop keeps them on the crawler and reads them back off ``crawl.crawler`` when
    the run is over.
    """

    crawler: Crawler
    stats: Stats = field(default_factory=Stats)
    #: Every request that failed, paired with its exception, up to and past
    #: ``max_errors``. A crawl that survived twenty failures shows all twenty
    #: here even though ``run()`` never raises for it.
    errors: list[tuple[Request, Exception]] = field(default_factory=list)
    #: Where every item this crawl emits is also written, under the crawler's
    #: name; the caller owns it — opens and closes it — and may share it.
    dataset: Dataset | None = None
    #: Keys of the requests this run has queued, for ``Settings.dedupe``.
    _seen: set[str] = field(default_factory=set, init=False, repr=False)
    #: Set only while ``stream()`` is iterating: the channel workers hand items
    #: to. Bounded, so a slow consumer blocks the worker that fed it.
    _out: asyncio.Queue[Any] | None = field(default=None, init=False, repr=False)
    #: The crawl behind a live ``stream()``, kept so it can be stopped even when
    #: the consumer walks away without closing the generator.
    _run_task: asyncio.Task[Stats] | None = field(default=None, init=False, repr=False)

    async def aclose(self) -> None:
        """Stop a crawl still running behind an abandoned ``stream()``.

        Breaking out of ``async for`` does not close the generator there and
        then — Python finalises it whenever it gets around to it, and until it
        does, the workers and the HTTP session they hold stay alive.
        ``open_crawl()`` calls this on the way out so that cannot happen;
        a direct ``stream()`` user wants ``contextlib.aclosing()``.
        """
        task = self._run_task
        self._run_task = None
        self._out = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def stream(self) -> AsyncIterator[Any]:
        """Yield items as the crawl produces them.

        Pull-based, where ``process_item()`` is push-based: the
        consumer's loop drives, and breaking out of it stops the crawl. The
        channel is bounded, so a slow consumer applies backpressure instead of
        piling items up in memory.

        The crawl's outcome surfaces after the last item: a clean finish ends
        the iteration, and a failure raises the first error, as ``run()`` does.
        Use it through ``open_crawl()``, which owns the HTTP session for as
        long as the iteration needs it.
        """
        out: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._buffer_size())
        self._out = out
        run_task = asyncio.create_task(self.run())
        self._run_task = run_task
        try:
            while True:
                get_task = asyncio.create_task(out.get())
                await asyncio.wait({get_task, run_task}, return_when=asyncio.FIRST_COMPLETED)
                if get_task.done():
                    yield get_task.result()
                    continue

                # The crawl is over. Cancelling the pending get can lose a race
                # against a value already handed to it, so claim that value
                # before draining what is still queued behind it.
                get_task.cancel()
                leftover: Any = _MISSING
                with contextlib.suppress(asyncio.CancelledError):
                    leftover = await get_task
                if leftover is not _MISSING:
                    yield leftover
                while not out.empty():
                    yield out.get_nowait()

                # Re-raises the first error, or returns the stats we ignore here.
                await run_task
                return
        finally:
            # Reached on a consumer's ``break`` too — but only once Python gets
            # around to finalising this generator, which is why aclose() exists
            # and why open_crawl() does not rely on this alone.
            self._out = None
            if self._run_task is run_task:
                self._run_task = None
            if not run_task.done():
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task

    def _buffer_size(self) -> int:
        """One parked item per worker: enough to keep them moving, bounded enough to push back."""
        return worker_count(self.crawler.ctx.params, self.crawler.settings.concurrency)

    async def run(self) -> Stats:
        """Run the crawl with ``concurrency`` workers; return its ``Stats``.

        A failed request neither kills a worker nor passes silently: it is
        collected in ``errors``. Up to ``max_errors`` of them the crawl carries
        on and returns its stats; one more stops it, and the first failure is
        raised once the workers have finished.
        """
        crawler = self.crawler
        params = crawler.ctx.params
        limit = read_max_requests(params, crawler.settings.max_requests)
        error_limit = read_max_errors(params, crawler.settings.max_errors)

        self.stats.started_at = time.monotonic()
        # Before start_requests(), not inside the try/finally below: if this
        # raises, nothing was opened, so closed() has nothing to undo — the
        # same rule ``async with`` follows when ``__aenter__`` fails.
        await crawler.opened()
        queue: asyncio.Queue[Request] = asyncio.Queue()
        async for req in crawler.start_requests():
            self._enqueue(req, queue)

        n_workers = worker_count(params, crawler.settings.concurrency)
        workers = [
            asyncio.create_task(self._worker(queue, limit, error_limit)) for _ in range(n_workers)
        ]
        try:
            await queue.join()
        except asyncio.CancelledError:
            # A crawl stopped from outside did not finish, and reporting 'done'
            # for it would be a lie a caller acts on — this is the state a
            # consumer's ``break`` leaves behind.
            self.stats.reason = 'cancelled'
            raise
        finally:
            # In a finally because a crawl can also end by being cancelled from
            # the outside, and workers left running would outlive the session
            # they hold.
            for w in workers:
                w.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*workers, return_exceptions=True)
            self.stats.finished_at = time.monotonic()
            flush_error = await self._finish()

        # The worker records the stop as it happens; one source for "failed".
        if self.stats.reason == 'max_errors':
            first = self.errors[0][1]
            first.add_note(
                f'crawl stopped after {self.stats.errors} failed requests '
                f'(max_errors={error_limit})'
            )
            if flush_error is not None:
                first.add_note(f'and flushing the dataset failed: {flush_error!r}')
            raise first
        if flush_error is not None:
            raise flush_error
        return self.stats

    async def _finish(self) -> Exception | None:
        """Flush the dataset and close the crawler, however the crawl ended.

        A flush failure is returned, not raised: raised from ``run()``'s
        ``finally`` it would replace a cancellation, or the request error the
        crawl is about to report. ``closed()`` runs either way.
        """
        try:
            if self.dataset is not None:
                # A batch still buffered is written even by a caller that never
                # closes the dataset — the engine does not own it.
                await self.dataset.flush()
        except Exception as exc:  # noqa: BLE001 — run() decides what it means
            logger.warning('crawl.flush_failed %r', exc)
            return exc
        finally:
            # Before any error is raised — a crawler that opened something in
            # __init__ still needs it closed even when the crawl fails.
            await self.crawler.closed(self.stats)
        return None

    def _enqueue(self, req: Request, queue: asyncio.Queue[Request]) -> None:
        """Queue a request unless this crawl has already queued one with its key.

        The one door into the queue, for start requests and callbacks alike. A
        request let through with ``dont_filter`` still records its key, so a
        plain request for the same page after it is a duplicate.
        """
        if self.crawler.settings.dedupe:
            key = request_key(req)
            if key in self._seen and not req.dont_filter:
                self.stats.duplicates += 1
                logger.debug('crawl.duplicate %s %s', req.method, req.url)
                return
            self._seen.add(key)
        queue.put_nowait(req)

    async def _worker(
        self, queue: asyncio.Queue[Request], limit: int | None, error_limit: int | None
    ) -> None:
        while True:
            req = await queue.get()
            try:
                if self.stats.reason == 'max_errors':
                    # Stopped for failures: drain without sending, as below, and
                    # before it, so reaching max_requests cannot overwrite why.
                    continue
                if limit is not None and self.stats.requests >= limit:
                    # Reached the cap: drain what is queued without sending it,
                    # so queue.join() still finishes and the crawl ends cleanly.
                    # Marked here rather than on reaching the count, so a crawl
                    # that ends exactly on the limit is still a plain 'done'.
                    self.stats.reason = 'max_requests'
                    continue
                # No await between the check and the increment, so on asyncio's
                # single thread the cap cannot be overshot by racing workers.
                self.stats.requests += 1
                await self._handle(req, queue)
            except Exception as exc:  # noqa: BLE001 — collect, don't kill the worker
                # Note the request on the exception itself, so the traceback
                # raised at the end of the crawl still says which page it was.
                exc.add_note(f'while handling {req.method} {req.url}')
                logger.warning('crawl.error %s %s %r', req.method, req.url, exc)
                self.stats.errors += 1
                self.errors.append((req, exc))
                if error_limit is not None and self.stats.errors > error_limit:
                    # One failure past what this crawl tolerates: stop sending.
                    # Before on_error, not after — a hook that awaits would let
                    # the other workers take and send more in the meantime.
                    # Requests they already took still go out.
                    self.stats.reason = 'max_errors'
                try:
                    await self.crawler.on_error(req, exc)
                except Exception:  # noqa: BLE001 — a broken hook must not also kill the worker
                    logger.exception('crawl.on_error_failed %s %s', req.method, req.url)
            finally:
                queue.task_done()

    async def _handle(self, req: Request, queue: asyncio.Queue[Request]) -> None:
        crawler = self.crawler
        raw = await crawler.http.request(req.method, req.url, **req.http_kwargs())
        response = Response(raw, req, crawler)
        callback = req.callback or crawler.parse
        async for result in callback(response):
            if isinstance(result, Request):
                self._enqueue(result, queue)
            else:
                # Counted here rather than in process_item: an override that
                # forgets super() must not silently corrupt the crawl's count.
                self.stats.items += 1
                await crawler.process_item(result)
                if self.dataset is not None:
                    await self.dataset.push_data(result, crawler=crawler.name)
                if self._out is not None:
                    # Bounded: this is where a slow stream consumer stops us.
                    await self._out.put(result)
