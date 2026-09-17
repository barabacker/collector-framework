"""Crawler — the engine that runs a parser: queue, workers, limits, stats.

Split out of ``BaseParser`` so that a parser stays declarative. A parser says
*what* to fetch and what to do with an item; the crawler owns everything about
one run — the queue, the workers, the counters and the failures — and is what
the caller gets back when the run is over.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from collector.params import read_concurrency, read_max_requests
from collector.request import Request
from collector.response import Response

if TYPE_CHECKING:
    from collector.parser import BaseParser

logger = logging.getLogger(__name__)

#: Distinguishes "no item" from an item that happens to be ``None``.
_MISSING = object()


@dataclass(slots=True)
class Stats:
    """What one crawl did. Times are ``time.monotonic()``, so only gaps mean anything."""

    #: Requests taken off the queue and sent. Retries happen inside the HTTP
    #: client and are invisible here, so this counts *requests the parser
    #: asked for*, not round trips curl made.
    requests: int = 0
    errors: int = 0
    items: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    #: Why the crawl ended: ``'done'`` (the queue drained), ``'max_requests'``
    #: (the ceiling was reached) or ``'cancelled'`` (something stopped it — a
    #: consumer that broke out of ``stream()``, or a cancel from outside).
    reason: str = 'done'

    @property
    def elapsed(self) -> float:
        """Seconds the crawl has been running, or ran for once finished."""
        end = time.monotonic() if self.finished_at is None else self.finished_at
        return end - self.started_at


@dataclass(slots=True)
class Crawler:
    """Runs one parser to completion and holds everything that run produced.

    ``on_item`` is the caller's own handler, run after the parser's
    ``process_item()`` — it is how ``collect()`` gathers items without
    subclassing the parser behind its back.
    """

    parser: BaseParser
    on_item: Callable[[Any], None] | None = None
    stats: Stats = field(default_factory=Stats)
    #: Every request that failed, paired with its exception. ``run()`` re-raises
    #: the first, but a crawl that survived twenty failures should show twenty.
    errors: list[tuple[Request, Exception]] = field(default_factory=list)
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
        ``open_crawler()`` calls this on the way out so that cannot happen;
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

        Pull-based, where ``process_item()`` and ``on_item`` are push-based: the
        consumer's loop drives, and breaking out of it stops the crawl. The
        channel is bounded, so a slow consumer applies backpressure instead of
        piling items up in memory.

        The crawl's outcome surfaces after the last item: a clean finish ends
        the iteration, and a failure raises the first error, as ``run()`` does.
        Use it through ``open_crawler()``, which owns the HTTP session for as
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
            # and why open_crawler() does not rely on this alone.
            self._out = None
            if self._run_task is run_task:
                self._run_task = None
            if not run_task.done():
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task

    def _buffer_size(self) -> int:
        """One parked item per worker: enough to keep them moving, bounded enough to push back."""
        return max(read_concurrency(self.parser.ctx.params, self.parser.settings.concurrency), 1)

    async def run(self) -> Stats:
        """Run the crawl with ``concurrency`` workers; return its ``Stats``.

        The first error collected while handling requests is re-raised once all
        workers have finished, so one bad page neither kills a worker nor passes
        silently; see ``errors`` for the rest.
        """
        parser = self.parser
        params = parser.ctx.params
        limit = read_max_requests(params, parser.settings.max_requests)

        self.stats.started_at = time.monotonic()
        queue: asyncio.Queue[Request] = asyncio.Queue()
        async for req in parser.start_requests():
            queue.put_nowait(req)

        n_workers = read_concurrency(params, parser.settings.concurrency)
        workers = [asyncio.create_task(self._worker(queue, limit)) for _ in range(n_workers)]
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

        if self.errors:
            raise self.errors[0][1]
        return self.stats

    async def _worker(self, queue: asyncio.Queue[Request], limit: int | None) -> None:
        while True:
            req = await queue.get()
            try:
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
            finally:
                queue.task_done()

    async def _handle(self, req: Request, queue: asyncio.Queue[Request]) -> None:
        parser = self.parser
        raw = await parser.http.request(req.method, req.url, **req.http_kwargs())
        response = Response(raw, req)
        callback = req.callback or parser.parse
        async for result in callback(response):
            if isinstance(result, Request):
                queue.put_nowait(result)
            else:
                # Counted here rather than in process_item: an override that
                # forgets super() must not silently corrupt the crawl's count.
                self.stats.items += 1
                await parser.process_item(result)
                if self.on_item is not None:
                    self.on_item(result)
                if self._out is not None:
                    # Bounded: this is where a slow stream consumer stops us.
                    await self._out.put(result)
