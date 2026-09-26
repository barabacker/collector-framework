"""Crawl.run(): queueing, item handling, concurrency, limits, stats and errors."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import replace
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import Crawl, Crawler, Request
from collector.storage import MemoryDataset

PAGE_1 = 'https://example.test/p1'
PAGE_2 = 'https://example.test/p2'


class _TwoPages(Crawler):
    """Emits one item per page and follows a single link from page 1."""

    name = 'two_pages'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        yield {'url': response.request.url}
        if response.request.url == PAGE_1:
            yield self.request(PAGE_2)


class _FailingBoth(Crawler):
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


async def test_a_sync_caller_reads_its_items_off_the_crawler(ctx_factory):
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
    class _WithMeta(Crawler):
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

    class _Api(Crawler):
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


# ── lifecycle ────────────────────────────────────────────────────────────────


async def test_opened_is_called_before_start_requests(ctx_factory):
    calls: list[str] = []

    class _Tracking(_TwoPages):
        async def opened(self) -> None:
            calls.append('opened')

        async def start_requests(self):
            calls.append('start_requests')
            async for req in super().start_requests():
                yield req

    ctx, _ = ctx_factory(FakeHttp())
    await Crawl(_Tracking(ctx)).run()

    assert calls == ['opened', 'start_requests']


async def test_closed_is_not_called_when_opened_fails(ctx_factory):
    """Nothing opened means nothing to close — mirrors ``async with``."""
    closed_calls: list[Any] = []

    class _Failing(_TwoPages):
        async def opened(self) -> None:
            raise ValueError('setup failed')

        async def closed(self, stats: Any) -> None:
            closed_calls.append(stats)

    ctx, _ = ctx_factory(FakeHttp())
    with pytest.raises(ValueError, match='setup failed'):
        await Crawl(_Failing(ctx)).run()

    assert closed_calls == []


async def test_closed_is_called_with_the_final_stats(ctx_factory):
    seen: list[Any] = []

    class _Tracking(_TwoPages):
        async def closed(self, stats: Any) -> None:
            seen.append(stats)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Tracking(ctx))
    stats = await crawl.run()

    assert seen == [stats]


async def test_closed_runs_even_when_the_crawl_fails(ctx_factory):
    """A crawler that opened a resource in __init__ still needs it closed."""
    seen: list[Any] = []

    class _Tracking(_FailingBoth):
        async def closed(self, stats: Any) -> None:
            seen.append(stats)

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Tracking(ctx))

    with pytest.raises(ValueError):
        await crawl.run()

    assert seen == [crawl.stats]


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


async def test_on_error_is_called_with_the_request_and_exception(ctx_factory):
    seen: list[Any] = []

    class _Tracking(_FailingBoth):
        async def on_error(self, request: Any, exc: Exception) -> None:
            seen.append((request, exc))

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Tracking(ctx))

    with pytest.raises(ValueError):
        await crawl.run()

    assert {req.url for req, _ in seen} == {PAGE_1, PAGE_2}
    assert all(isinstance(exc, ValueError) for _, exc in seen)
    # A no-op by default, but it did not replace the framework's own bookkeeping.
    assert len(crawl.errors) == 2


async def test_a_broken_on_error_does_not_kill_the_worker(ctx_factory):
    """A hook failing must not be worse than the failure it was reacting to."""
    calls = 0

    class _Failing(_FailingBoth):
        start_urls = [PAGE_1]

        async def on_error(self, request: Any, exc: Exception) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError('on_error is broken')

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_Failing(ctx))

    # The original failure still ends the crawl — on_error breaking does not
    # substitute its own error for it, or leave the crawl hanging.
    with pytest.raises(ValueError, match='bad'):
        await crawl.run()

    assert calls == 1
    assert len(crawl.errors) == 1


# ── stream ──────────────────────────────────────────────────────────────────


class _FiveItems(Crawler):
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


async def test_stream_still_runs_the_crawlers_process_item(ctx_factory):
    """Streaming is another consumer, not a replacement for the crawler's hook."""
    pushed: list[Any] = []

    class _Noting(_FiveItems):
        async def process_item(self, item: Any) -> None:
            pushed.append(item)

    ctx, _ = ctx_factory(FakeHttp())
    streamed = [item async for item in Crawl(_Noting(ctx)).stream()]

    assert pushed == streamed


async def test_stream_raises_after_yielding_what_succeeded(ctx_factory):
    """The crawl's outcome surfaces at the end, exactly as run() does."""

    class _ItemThenFail(Crawler):
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


class _Endless(Crawler):
    """Re-requests its one page for as long as anyone keeps listening."""

    name = 'endless'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        yield {'tick': True}
        # dont_filter: the repeat is the point, and de-duplication would
        # otherwise let the crawl finish after the first page.
        yield self.request(PAGE_1, dont_filter=True)


async def test_breaking_out_stops_an_endless_crawl(ctx_factory):
    """The whole point of pull: the consumer decides when enough is enough."""

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


# ── de-duplication ───────────────────────────────────────────────────────────


class _Linking(Crawler):
    """Page 1 links to page 2 twice; page 2 links back to page 1."""

    name = 'linking'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        if response.request.url == PAGE_1:
            yield self.request(PAGE_2)
            yield self.request(PAGE_2)
        else:
            yield self.request(PAGE_1)


async def test_a_request_already_queued_is_not_sent_again(ctx_factory):
    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Linking(ctx)).run()

    assert sorted(url for _, url in http.calls) == [PAGE_1, PAGE_2]
    assert stats.requests == 2
    assert stats.duplicates == 2


async def test_a_duplicate_start_url_is_dropped_too(ctx_factory):
    class _Twice(_TwoPages):
        start_urls = [PAGE_1, PAGE_1]

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Twice(ctx)).run()

    assert [url for _, url in http.calls].count(PAGE_1) == 1
    assert stats.duplicates == 1


async def test_dont_filter_sends_a_request_again(ctx_factory):
    class _Again(Crawler):
        name = 'again'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            if not response.metadata.get('again'):
                yield self.request(PAGE_1, dont_filter=True, metadata={'again': True})

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Again(ctx)).run()

    assert http.calls == [('GET', PAGE_1), ('GET', PAGE_1)]
    assert stats.duplicates == 0


async def test_with_dedupe_off_every_duplicate_is_sent(ctx_factory):
    class _Loose(_Linking):
        settings = replace(_Linking.settings, dedupe=False, max_requests=5)

    ctx, _ = ctx_factory(FakeHttp())

    stats = await Crawl(_Loose(ctx)).run()

    assert stats.requests == 5
    assert stats.duplicates == 0


async def test_posts_to_one_url_with_different_bodies_are_all_sent(ctx_factory):
    class _Pager(Crawler):
        name = 'pager'

        async def start_requests(self):
            for target in ('pager$2', 'pager$3'):
                yield self.request(PAGE_1, method='POST', data={'__EVENTTARGET': target})

        async def parse(self, response: Any):
            yield {}

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Pager(ctx)).run()

    assert http.calls == [('POST', PAGE_1), ('POST', PAGE_1)]
    assert stats.duplicates == 0


async def test_duplicates_do_not_use_up_max_requests(ctx_factory):
    class _Capped(_Linking):
        settings = replace(_Linking.settings, max_requests=2)

    ctx, _ = ctx_factory(FakeHttp())

    stats = await Crawl(_Capped(ctx)).run()

    # Two distinct pages exactly fill the ceiling; the dropped duplicates were
    # never queued, so the crawl drained rather than hitting the limit.
    assert (stats.requests, stats.duplicates, stats.reason) == (2, 2, 'done')


async def test_a_request_sent_with_dont_filter_still_counts_as_seen(ctx_factory):
    class _ForcedThenPlain(Crawler):
        name = 'forced_then_plain'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            if response.request.url == PAGE_1:
                yield self.request(PAGE_2, dont_filter=True)
                yield self.request(PAGE_2)

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_ForcedThenPlain(ctx)).run()

    assert [url for _, url in http.calls].count(PAGE_2) == 1
    assert stats.duplicates == 1


# ── datasets ─────────────────────────────────────────────────────────────────


async def test_a_crawl_stores_every_item_under_the_crawlers_name(ctx_factory):
    dataset = MemoryDataset()
    ctx, _ = ctx_factory(FakeHttp())

    await Crawl(_TwoPages(ctx), dataset=dataset).run()

    stored = [item['url'] async for item in dataset.iterate_items(crawler='two_pages')]
    assert stored == [PAGE_1, PAGE_2]


async def test_the_dataset_is_flushed_once_when_the_crawl_ends(ctx_factory):
    class _Counting(MemoryDataset):
        flushes = 0

        async def flush(self) -> None:
            self.flushes += 1

    dataset = _Counting()
    ctx, _ = ctx_factory(FakeHttp())

    await Crawl(_TwoPages(ctx), dataset=dataset).run()

    assert dataset.flushes == 1


async def test_a_dataset_that_refuses_an_item_fails_that_request(ctx_factory):
    class _Full(MemoryDataset):
        async def push_data(self, item: Any, *, crawler: str) -> None:
            raise OSError('disk full')

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_TwoPages(ctx), dataset=_Full())

    with pytest.raises(OSError, match='disk full'):
        await crawl.run()

    # Page 1's item fails before its callback gets to the link to page 2, so
    # one request, one failure.
    assert len(crawl.errors) == 1


async def test_a_streamed_crawl_stores_the_items_it_streams(ctx_factory):
    dataset = MemoryDataset()
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_TwoPages(ctx), dataset=dataset)

    streamed = [item async for item in crawl.stream()]

    assert streamed == [item async for item in dataset.iterate_items()]


class _BrokenFlush(MemoryDataset):
    async def flush(self) -> None:
        raise OSError('disk full')


async def test_a_dataset_that_cannot_flush_fails_the_crawl(ctx_factory):
    closed: list[str] = []

    class _Closing(_TwoPages):
        async def closed(self, stats: Any) -> None:
            closed.append(stats.reason)

    ctx, _ = ctx_factory(FakeHttp())

    with pytest.raises(OSError, match='disk full'):
        await Crawl(_Closing(ctx), dataset=_BrokenFlush()).run()

    assert closed == ['done']


async def test_a_failed_flush_does_not_hide_the_first_request_error(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_FailingBoth(ctx), dataset=_BrokenFlush())

    with pytest.raises(ValueError, match='bad') as info:
        await crawl.run()

    assert any('flushing the dataset failed' in note for note in info.value.__notes__)
