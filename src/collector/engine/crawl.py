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
from collector.engine.pipeline import PipelineManager

if TYPE_CHECKING:
    from collector.crawler.crawler import Crawler

logger = logging.getLogger(__name__)

#: Distinguishes "no item" from an item that happens to be ``None``.
_MISSING = object()


class _PipelineFailure(Exception):
    """Internal boundary marker; callers always receive the original failure."""

    def __init__(self, error: Exception) -> None:
        self.error = error


async def _stop_task(task: asyncio.Task[Any]) -> None:
    """Stop a stream's run, observing late failures and preserving caller cancellation."""
    caller = asyncio.current_task()
    previous_cancels = caller.cancelling() if caller is not None else 0
    if not task.done():
        task.cancel()
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if caller is not None and caller.cancelling() > previous_cancels:
                cancellation = exc
            if task.cancelled():
                break
            cancellation = cancellation or exc
        except Exception:
            break
    try:
        task.result()
    except asyncio.CancelledError:
        pass  # The cancellation we requested, not cancellation of this caller.
    except Exception as exc:
        if cancellation is None:
            raise
        cancellation.add_note(f'crawl failed while stopping: {exc!r}')
    if cancellation is not None:
        raise cancellation


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
    #: it tolerates), ``'pipeline_error'`` (a component failed), or
    #: ``'cancelled'`` (something stopped it — a
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

    An item passes through the crawler's ``process_item()`` hook and the
    configured pipelines before ``stream()`` pulls it. A caller who wants
    the items without writing an async
    loop keeps them on the crawler and reads them back off ``crawl.crawler`` when
    the run is over.
    """

    crawler: Crawler
    stats: Stats = field(default_factory=Stats)
    #: Every request that failed, paired with its exception, up to and past
    #: ``max_errors``. A crawl that survived twenty failures shows all twenty
    #: here even though ``run()`` never raises for it.
    errors: list[tuple[Request, Exception]] = field(default_factory=list)
    #: Keys of the requests this run has queued, for ``Settings.dedupe``.
    _seen: set[str] = field(default_factory=set, init=False, repr=False)
    #: Set only while ``stream()`` is iterating: the channel workers hand items
    #: to. Bounded, so a slow consumer blocks the worker that fed it.
    _out: asyncio.Queue[Any] | None = field(default=None, init=False, repr=False)
    #: The crawl behind a live ``stream()``, kept so it can be stopped even when
    #: the consumer walks away without closing the generator.
    _run_task: asyncio.Task[Stats] | None = field(default=None, init=False, repr=False)
    _pipelines: PipelineManager = field(init=False, repr=False)
    _fatal: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _fatal_error: BaseException | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._pipelines = PipelineManager(self.crawler)

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
        if task is not None:
            await _stop_task(task)

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
        get_task: asyncio.Task[Any] | None = None
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

                # Raises the first error if the crawl failed, or returns the
                # stats, which we ignore here.
                await run_task
                return
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
            # Reached on a consumer's ``break`` too — but only once Python gets
            # around to finalising this generator, which is why aclose() exists
            # and why open_crawl() does not rely on this alone.
            self._out = None
            if self._run_task is run_task:
                self._run_task = None
                try:
                    if get_task is not None:
                        with contextlib.suppress(asyncio.CancelledError):
                            await get_task
                finally:
                    await _stop_task(run_task)

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
        queue: asyncio.Queue[Request] = asyncio.Queue()
        workers: list[asyncio.Task[None]] = []
        waiters: list[asyncio.Task[Any]] = []
        opened = False
        failure: BaseException | None = None
        try:
            try:
                await self._pipelines.open()
            except Exception:
                self.stats.reason = 'pipeline_error'
                raise
            await crawler.opened()
            opened = True
            async for req in crawler.start_requests():
                self._enqueue(req, queue)
            n_workers = worker_count(params, crawler.settings.concurrency)
            workers = [
                asyncio.create_task(self._worker(queue, limit, error_limit))
                for _ in range(n_workers)
            ]
            waiters = [asyncio.create_task(queue.join()), asyncio.create_task(self._fatal.wait())]
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if self._fatal_error is not None:
                raise self._fatal_error
            if self.stats.reason == 'max_errors':
                first = self.errors[0][1]
                first.add_note(
                    f'crawl stopped after {self.stats.errors} failed requests '
                    f'(max_errors={error_limit})'
                )
                raise first
        except BaseException as exc:
            failure = exc
            if isinstance(exc, asyncio.CancelledError):
                self.stats.reason = 'cancelled'

        # A separately owned task makes cleanup survive repeated cancellation.
        # Keep waiting for it: shield alone would leave a background cleanup.
        cleanup = asyncio.create_task(self._finish(workers, waiters, opened))
        while True:
            try:
                cleanup_errors = await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError as exc:
                if cleanup.cancelled():
                    raise
                self.stats.reason = 'cancelled'
                if failure is not None and not isinstance(failure, asyncio.CancelledError):
                    exc.add_note(f'crawl was already failing: {failure!r}')
                failure = exc
        self.stats.finished_at = time.monotonic()
        for stage, error in cleanup_errors:
            logger.warning('crawl.cleanup_failed %s %r', stage, error)
            if isinstance(error, asyncio.CancelledError):
                self.stats.reason = 'cancelled'
                if failure is not None and failure is not error:
                    error.add_note(f'crawl was already failing: {failure!r}')
                failure = error
            elif failure is None:
                failure = error
                if stage == 'pipeline':
                    self.stats.reason = 'pipeline_error'
            else:
                failure.add_note(f'and {stage} cleanup failed: {error!r}')
        if failure is not None:
            raise failure
        return self.stats

    async def _finish(
        self, workers: list[asyncio.Task[None]], waiters: list[asyncio.Task[Any]], opened: bool
    ) -> list[tuple[str, BaseException]]:
        """Stop users of resources before closing their owners."""
        for task in [*workers, *waiters]:
            task.cancel()
        await asyncio.gather(*workers, *waiters, return_exceptions=True)
        self.stats.finished_at = time.monotonic()
        errors: list[tuple[str, BaseException]] = []
        if opened:
            try:
                await self.crawler.closed(self.stats)
            except (Exception, asyncio.CancelledError) as exc:
                errors.append(('crawler', exc))
        errors.extend(('pipeline', exc) for exc in await self._pipelines.close())
        return errors

    def _enqueue(self, req: Request, queue: asyncio.Queue[Request]) -> None:
        """Queue a request unless this crawl has already queued one with its key.

        The one door into the queue, for start requests and callbacks alike. A
        request let through with ``dont_filter`` still records its key, so a
        plain request for the same page after it is a duplicate.
        """
        if self._fatal.is_set():
            return
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
                if self._fatal.is_set():
                    return
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
            except asyncio.CancelledError as exc:
                # A component can cancel its own worker. Wake the run rather
                # than silently lose a worker and leave queue.join() waiting.
                if self._fatal_error is None:
                    self._fatal_error = exc
                    self.stats.reason = 'cancelled'
                    self._fatal.set()
                raise
            except _PipelineFailure as failed:
                exc = failed.error
                exc.add_note(f'while handling {req.method} {req.url} in an item pipeline')
                logger.warning('crawl.pipeline_error %s %s %r', req.method, req.url, exc)
                self.stats.errors += 1
                self.errors.append((req, exc))
                return
            except Exception as exc:  # noqa: BLE001 — collect, don't kill the worker
                # Note the request on the exception itself, so the traceback
                # raised at the end of the crawl still says which page it was.
                exc.add_note(f'while handling {req.method} {req.url}')
                logger.warning('crawl.error %s %s %r', req.method, req.url, exc)
                self.stats.errors += 1
                self.errors.append((req, exc))
                if (
                    not self._fatal.is_set()
                    and error_limit is not None
                    and self.stats.errors > error_limit
                ):
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
                try:
                    result = await self._pipelines.process(result)
                except Exception as exc:
                    # Publish before any await so workers cannot start more requests.
                    if self._fatal_error is None:
                        self._fatal_error = exc
                        self.stats.reason = 'pipeline_error'
                        self._fatal.set()
                    raise _PipelineFailure(exc) from exc
                if self._out is not None:
                    # Bounded: this is where a slow stream consumer stops us.
                    await self._out.put(result)
