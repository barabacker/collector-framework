"""Crawl.run(): queueing, item handling, concurrency, limits, stats and errors."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import replace
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import Crawl, Parser, Request

PAGE_1 = 'https://example.test/p1'
PAGE_2 = 'https://example.test/p2'


class _TwoPages(Parser):
    """Emits one item per page and follows a single link from page 1."""

    name = 'two_pages'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        yield {'url': response.request.url}
        if response.request.url == PAGE_1:
            yield self.request(PAGE_2)


class _FailingBoth(Parser):
    name = 'failing_all'
    start_urls = [PAGE_1, PAGE_2]

    async def parse(self, response: Any):
        raise ValueError(f'bad {response.request.url}')
        yield  # pragma: no cover — unreachable, keeps this a generator


# ── queueing and items ──────────────────────────────────────────────────────


async def test_run_follows_requests_and_counts_items(ctx_factory):
    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_TwoPages(ctx)).run()

    assert stats.items == 2
    assert stats.requests == 2
    assert {url for _, url in http.calls} == {PAGE_1, PAGE_2}


async def test_process_item_override_receives_every_item(ctx_factory):
    seen: list[Any] = []

    class _Collecting(_TwoPages):
        async def process_item(self, item: Any) -> None:
            await super().process_item(item)
            seen.append(item)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Collecting(ctx))
    await crawl.run()

    assert [item['url'] for item in seen] == [PAGE_1, PAGE_2]
    assert crawl.stats.items == 2


async def test_a_sync_caller_reads_its_items_off_the_parser(ctx_factory):
    """The one push path: keep them on self, read them back from crawl.crawler."""

    class _Keeping(_TwoPages):
        def __init__(self, ctx: Any) -> None:
            super().__init__(ctx)
            self.kept: list[Any] = []

        async def process_item(self, item: Any) -> None:
            self.kept.append(item)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Keeping(ctx))
    await crawl.run()

    assert [item['url'] for item in crawl.crawler.kept] == [PAGE_1, PAGE_2]


async def test_item_count_survives_an_override_that_forgets_super(ctx_factory):
    class _Sloppy(_TwoPages):
        async def process_item(self, item: Any) -> None:
            pass  # no super() call

    ctx, _ = ctx_factory(FakeHttp())
    assert (await Crawl(_Sloppy(ctx)).run()).items == 2


async def test_callback_metadata_reaches_the_response(ctx_factory):
    class _WithMeta(Parser):
        name = 'with_meta'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            yield self.request(PAGE_2, callback=self.parse_detail, metadata={'page': 7})

        async def parse_detail(self, response: Any):
            yield {'page': response.metadata['page']}

    ctx, _ = ctx_factory(FakeHttp())
    seen = [item async for item in Crawl(_WithMeta(ctx)).stream()]

    assert seen == [{'page': 7}]


async def test_request_fields_reach_the_http_client(ctx_factory):
    """params/json/cookies are per-request, so they must survive the queue."""

    class _Api(Parser):
        name = 'api'

        async def start_requests(self):
            yield self.request(
                PAGE_1,
                method='POST',
                params={'page': 2},
                json={'q': 'лот'},
                cookies={'session': 'abc'},
            )

        async def parse(self, response: Any):
            yield {'ok': True}

    http = FakeHttp()
    ctx, _ = ctx_factory(http)
    await Crawl(_Api(ctx)).run()

    assert http.calls == [('POST', PAGE_1)]
    assert http.sent == [
        {'params': {'page': 2}, 'json': {'q': 'лот'}, 'cookies': {'session': 'abc'}}
    ]


async def test_a_plain_request_sends_no_empty_transport_kwargs(ctx_factory):
    http = FakeHttp()
    ctx, _ = ctx_factory(http)
    await Crawl(_TwoPages(ctx)).run()

    assert http.sent == [{}, {}]


# ── limits ──────────────────────────────────────────────────────────────────


async def test_max_requests_stops_the_crawl(ctx_factory):
    """A pagination bug otherwise crawls forever with nothing to stop it."""

    class _Capped(_TwoPages):
        settings = replace(_TwoPages.settings, max_requests=1)

    http = FakeHttp()
    ctx, _ = ctx_factory(http)
    stats = await Crawl(_Capped(ctx)).run()

    assert stats.requests == 1
    assert stats.items == 1
    assert stats.reason == 'max_requests'
    assert http.calls == [('GET', PAGE_1)]


async def test_a_crawl_ending_exactly_on_the_limit_is_still_done(ctx_factory):
    """Reaching the cap is not stopping at it — nothing was refused."""

    class _Capped(_TwoPages):
        settings = replace(_TwoPages.settings, max_requests=2)

    ctx, _ = ctx_factory(FakeHttp())
    stats = await Crawl(_Capped(ctx)).run()

    assert stats.requests == 2
    assert stats.reason == 'done'


async def test_max_requests_param_overrides_the_setting(ctx_factory):
    class _Capped(_TwoPages):
        settings = replace(_TwoPages.settings, max_requests=1)

    ctx, _ = ctx_factory(FakeHttp(), params={'max_requests': '2'})
    stats = await Crawl(_Capped(ctx)).run()

    assert stats.requests == 2
    assert stats.reason == 'done'


async def test_no_limit_by_default(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    assert _TwoPages(ctx).settings.max_requests is None


# ── stats ───────────────────────────────────────────────────────────────────


async def test_stats_time_the_run(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_TwoPages(ctx))
    stats = await crawl.run()

    assert stats.finished_at is not None
    assert stats.elapsed >= 0.0

    # Finished means frozen: elapsed stops moving once the crawl is over.
    settled = stats.elapsed
    await asyncio.sleep(0.01)
    assert stats.elapsed == settled


async def test_stats_count_errors_alongside_the_error_list(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_FailingBoth(ctx))

    with pytest.raises(ValueError):
        await crawl.run()

    assert crawl.stats.errors == 2 == len(crawl.errors)
    assert crawl.stats.requests == 2
    assert crawl.stats.items == 0


# ── concurrency ─────────────────────────────────────────────────────────────


async def test_settings_concurrency_is_the_default_and_params_win(ctx_factory):
    class _Parallel(_TwoPages):
        settings = replace(_TwoPages.settings, concurrency=4)

    ctx, _ = ctx_factory(FakeHttp())
    assert _Parallel(ctx).settings.concurrency == 4

    ctx, _ = ctx_factory(FakeHttp(), params={'concurrency': '2'})
    assert (await Crawl(_Parallel(ctx)).run()).items == 2


async def test_cancelling_a_crawl_leaves_no_workers_behind(ctx_factory):
    """Workers outliving the crawl would keep the HTTP session alive."""

    class _SlowHttp(FakeHttp):
        async def request(self, method: str, url: str, **kwargs: Any) -> Any:
            await asyncio.sleep(10)
            raise AssertionError('never reached')  # pragma: no cover

    ctx, _ = ctx_factory(_SlowHttp())
    task = asyncio.create_task(Crawl(_TwoPages(ctx)).run())
    await asyncio.sleep(0)
    before = len(asyncio.all_tasks())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    remaining = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert remaining == []
    assert before > 1  # the workers really had started


# ── errors ──────────────────────────────────────────────────────────────────


async def test_one_bad_page_does_not_kill_the_worker(ctx_factory):
    class _Failing(_TwoPages):
        name = 'failing'
        # PAGE_2 is only reachable through PAGE_1, which fails — so seed both.
        start_urls = [PAGE_1, PAGE_2]

        async def parse(self, response: Any):
            if response.request.url == PAGE_1:
                raise ValueError('bad page')
            yield {'ok': True}

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Failing(ctx))

    with pytest.raises(ValueError, match='bad page'):
        await crawl.run()

    assert crawl.stats.items == 1


async def test_every_error_is_collected_with_its_request(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_FailingBoth(ctx))

    with pytest.raises(ValueError):
        await crawl.run()

    assert len(crawl.errors) == 2
    assert {req.url for req, _ in crawl.errors} == {PAGE_1, PAGE_2}
    assert all(isinstance(exc, ValueError) for _, exc in crawl.errors)
    assert all(isinstance(req, Request) for req, _ in crawl.errors)


async def test_the_reraised_error_carries_the_failing_url(ctx_factory):
    class _Failing(_FailingBoth):
        start_urls = [PAGE_1]

    ctx, _ = ctx_factory(FakeHttp())
    with pytest.raises(ValueError) as excinfo:
        await Crawl(_Failing(ctx)).run()

    assert f'while handling GET {PAGE_1}' in excinfo.value.__notes__


async def test_errors_are_logged_as_they_happen(ctx_factory, caplog):
    """A long crawl must not stay silent until it ends."""

    class _Failing(_FailingBoth):
        start_urls = [PAGE_1]

    ctx, _ = ctx_factory(FakeHttp())
    with caplog.at_level('WARNING', logger='collector.engine.crawl'), pytest.raises(ValueError):
        await Crawl(_Failing(ctx)).run()

    assert f'crawl.error GET {PAGE_1}' in caplog.text


# ── stream ──────────────────────────────────────────────────────────────────


class _FiveItems(Parser):
    name = 'five'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        for n in range(5):
            yield {'n': n}


async def test_stream_yields_every_item(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_FiveItems(ctx))

    got = [item async for item in crawl.stream()]

    assert got == [{'n': n} for n in range(5)]
    assert crawl.stats.items == 5
    assert crawl.stats.reason == 'done'


async def test_stream_yields_across_pages(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    got = [item async for item in Crawl(_TwoPages(ctx)).stream()]
    assert {item['url'] for item in got} == {PAGE_1, PAGE_2}


async def test_stream_still_runs_the_parsers_process_item(ctx_factory):
    """Streaming is another consumer, not a replacement for the parser's hook."""
    pushed: list[Any] = []

    class _Noting(_FiveItems):
        async def process_item(self, item: Any) -> None:
            pushed.append(item)

    ctx, _ = ctx_factory(FakeHttp())
    streamed = [item async for item in Crawl(_Noting(ctx)).stream()]

    assert pushed == streamed


async def test_stream_raises_after_yielding_what_succeeded(ctx_factory):
    """The crawl's outcome surfaces at the end, exactly as run() does."""

    class _ItemThenFail(Parser):
        name = 'item_then_fail'
        start_urls = [PAGE_1, PAGE_2]

        async def parse(self, response: Any):
            if response.request.url == PAGE_2:
                raise ValueError('boom')
            yield {'ok': True}

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_ItemThenFail(ctx))

    got: list[Any] = []
    with pytest.raises(ValueError, match='boom'):
        async for item in crawl.stream():
            got.append(item)

    assert got == [{'ok': True}]
    assert len(crawl.errors) == 1


async def test_breaking_out_stops_an_endless_crawl(ctx_factory):
    """The whole point of pull: the consumer decides when enough is enough."""

    class _Endless(Parser):
        name = 'endless'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            yield {'tick': True}
            yield self.request(PAGE_1)

    http = FakeHttp()
    ctx, _ = ctx_factory(http)
    crawl = Crawl(_Endless(ctx))

    got = []
    # A timeout rather than a hang if backpressure or cancellation regress.
    async with asyncio.timeout(5), aclosing(crawl.stream()) as stream:
        async for item in stream:
            got.append(item)
            if len(got) == 3:
                break

    await asyncio.sleep(0)
    assert len(got) == 3
    # The crawl cannot have run away: the bounded channel held it to the
    # consumer's pace, and closing the generator cancelled it.
    assert len(http.calls) < 10
    remaining = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert remaining == []


async def test_an_abandoned_stream_leaves_the_crawl_running(ctx_factory):
    """Why aclose() exists: break alone does not finalise the generator."""

    class _Endless(Parser):
        name = 'endless_leak'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            yield {'tick': True}
            yield self.request(PAGE_1)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Endless(ctx))

    async for _item in crawl.stream():
        break  # no aclosing(), no open_crawl() — the crawl is still going

    await asyncio.sleep(0)
    run_task = crawl._run_task
    assert run_task is not None and not run_task.done()

    await crawl.aclose()
    assert crawl._run_task is None
    assert run_task.cancelled()


async def test_a_stopped_crawl_does_not_report_itself_as_done(ctx_factory):
    """'done' means the queue drained. A consumer that walked away is not that."""

    class _Endless(Parser):
        name = 'endless_reason'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            yield {'tick': True}
            yield self.request(PAGE_1)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Endless(ctx))

    async for _item in crawl.stream():
        break
    await crawl.aclose()

    assert crawl.stats.reason == 'cancelled'


async def test_a_crawl_cancelled_from_outside_says_so(ctx_factory):
    class _SlowHttp(FakeHttp):
        async def request(self, method: str, url: str, **kwargs: Any) -> Any:
            await asyncio.sleep(10)
            raise AssertionError('never reached')  # pragma: no cover

    ctx, _ = ctx_factory(_SlowHttp())
    crawl = Crawl(_TwoPages(ctx))
    task = asyncio.create_task(crawl.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert crawl.stats.reason == 'cancelled'


async def test_stream_respects_max_requests(ctx_factory):
    class _Capped(_TwoPages):
        settings = replace(_TwoPages.settings, max_requests=1)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Capped(ctx))
    got = [item async for item in crawl.stream()]

    assert got == [{'url': PAGE_1}]
    assert crawl.stats.reason == 'max_requests'


async def test_the_channel_is_bounded_by_concurrency(ctx_factory):
    """Unbounded would buffer the whole crawl in memory and drop backpressure."""
    ctx, _ = ctx_factory(FakeHttp(), params={'concurrency': '3'})
    assert Crawl(_TwoPages(ctx))._buffer_size() == 3

    ctx, _ = ctx_factory(FakeHttp())
    assert Crawl(_TwoPages(ctx))._buffer_size() == 1


async def test_a_slow_consumer_holds_the_crawl_back(ctx_factory):
    """Backpressure: with a buffer of one, the crawl cannot outrun the reader."""
    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    seen = 0
    async for _item in Crawl(_FiveItems(ctx)).stream():
        seen += 1
        if seen == 1:
            # One item read, one parked in the channel — the worker is blocked
            # on the third rather than having produced all five.
            assert http.calls == [('GET', PAGE_1)]
        await asyncio.sleep(0)

    assert seen == 5


async def test_a_crawl_declaring_no_workers_still_runs(ctx_factory):
    """Zero workers meant a queue nobody drained, and run() waited on it for ever."""

    class _NoWorkers(_TwoPages):
        settings = replace(_TwoPages.settings, concurrency=0)

    ctx, _ = ctx_factory(FakeHttp())
    async with asyncio.timeout(5):
        stats = await Crawl(_NoWorkers(ctx)).run()

    assert stats.items == 2
    assert stats.reason == 'done'


async def test_a_bad_concurrency_param_cannot_stall_the_crawl(ctx_factory):
    """The param falls back to Settings, which may itself be unusable."""

    class _NoWorkers(_TwoPages):
        settings = replace(_TwoPages.settings, concurrency=0)

    ctx, _ = ctx_factory(FakeHttp(), params={'concurrency': 'lots'})
    async with asyncio.timeout(5):
        assert (await Crawl(_NoWorkers(ctx)).run()).items == 2
