"""Pipeline effects through the real engine, with only HTTP replaced."""

import asyncio
from dataclasses import replace

import pytest
from tests.conftest import FakeHttp, SessionHttp

from collector import (
    Crawl,
    Crawler,
    CrawlerContext,
    CrawlError,
    Settings,
    collect,
    crawl_many,
    open_crawl,
)


class One(Crawler):
    name = 'one'
    start_urls = ['https://example.test/']

    async def parse(self, response):
        yield 1


def run_for(cls=One, *, pipelines=(), http=None, **settings):
    instance = cls(CrawlerContext(http=http or FakeHttp()))
    instance.settings = replace(instance.settings, pipelines=pipelines, **settings)
    return Crawl(instance)


async def test_resources_survive_until_crawler_closed_and_items_are_transformed():
    events = []

    class Save:
        @classmethod
        def from_crawler(cls, crawler):
            result = cls()
            result.crawler = crawler
            return result

        def open_crawler(self):
            events.append('pipeline open')
            self.crawler.ctx.sink = events

        async def process_item(self, item):
            events.append(('pipeline', item))
            return item + 1

        def close_crawler(self):
            events.append('pipeline close')

    class Uses(One):
        async def opened(self):
            assert self.ctx.sink is events
            events.append('opened')

        async def start_requests(self):
            assert self.ctx.sink is events
            events.append('start')
            async for request in super().start_requests():
                yield request

        async def process_item(self, item):
            events.append(('legacy', item))
            return 100  # The old hook is not a transformer.

        async def closed(self, stats):
            events.append('closed')

    run = run_for(Uses, pipelines=(Save,))
    assert [item async for item in run.stream()] == [2]
    assert events == [
        'pipeline open',
        'opened',
        'start',
        ('legacy', 1),
        ('pipeline', 1),
        'closed',
        'pipeline close',
    ]
    assert run.stats.items == 1


@pytest.mark.parametrize('phase', ['factory', 'open', 'process', 'close'])
async def test_pipeline_failure_keeps_original_error_and_stats(monkeypatch, phase):
    failure = RuntimeError(phase)

    class Broken:
        @classmethod
        def from_crawler(cls, crawler):
            if phase == 'factory':
                raise failure
            return cls()

        def open_crawler(self):
            if phase == 'open':
                raise failure

        def process_item(self, item):
            if phase == 'process':
                raise failure
            return item

        def close_crawler(self):
            if phase == 'close':
                raise failure

    class Configured(One):
        settings = Settings(pipelines=(Broken,), max_errors=None)

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())
    with pytest.raises(CrawlError) as caught:
        async with open_crawl(Configured) as run:
            await asyncio.wait_for(run.run(), 2)
    assert caught.value.__cause__ is failure
    assert caught.value.crawl is run
    assert run.stats.reason == 'pipeline_error'
    assert run.stats.finished_at is not None
    assert run.stats.errors == (1 if phase == 'process' else 0)
    assert len(run.errors) == (1 if phase == 'process' else 0)
    if phase == 'process':
        assert run.errors[0][1] is failure


async def test_fatal_pipeline_cancels_other_workers_before_closing():
    busy = asyncio.Event()
    events = []
    failure = ValueError('write')

    class Blocking:
        async def process_item(self, item):
            if item == 'slow':
                busy.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append('worker stopped')
            await busy.wait()
            raise failure

        def close_crawler(self):
            events.append('close')

    class Several(One):
        start_urls = [f'https://example.test/{n}' for n in range(5)]

        async def parse(self, response):
            yield 'slow' if response.request.url.endswith('/0') else 'fail'

    run = run_for(Several, pipelines=(Blocking,), concurrency=2, max_errors=None)
    with pytest.raises(ValueError) as caught:
        await asyncio.wait_for(run.run(), 2)
    assert caught.value is failure
    assert run.stats.requests == 2
    assert events == ['worker stopped', 'close']


async def test_cleanup_errors_do_not_replace_primary_error(caplog):
    primary = ValueError('write')

    class Fails:
        def process_item(self, item):
            raise primary

        def close_crawler(self):
            raise RuntimeError('close component')

    class ClosedFails(One):
        async def closed(self, stats):
            raise RuntimeError('close crawler')

    run = run_for(ClosedFails, pipelines=(Fails,))
    with pytest.raises(ValueError) as caught:
        await run.run()
    assert caught.value is primary
    assert 'close component' in str(primary.__notes__)
    assert 'close crawler' in str(primary.__notes__)
    assert 'close component' in caplog.text


async def test_repeat_cancellation_waits_for_cleanup():
    processing = asyncio.Event()
    closing = asyncio.Event()
    release = asyncio.Event()
    events = []

    class Slow:
        async def process_item(self, item):
            processing.set()
            await asyncio.Event().wait()

        async def close_crawler(self):
            closing.set()
            await release.wait()
            events.append('closed')

    run = run_for(pipelines=(Slow,))
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(processing.wait(), 2)
    task.cancel()
    await asyncio.wait_for(closing.wait(), 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert events == ['closed']
    assert run.stats.reason == 'cancelled'


async def test_cancelled_pipeline_worker_stops_queued_requests():
    class Cancel:
        async def process_item(self, item):
            raise asyncio.CancelledError()

    class Several(One):
        start_urls = ['https://example.test/first', 'https://example.test/second']

    run = run_for(Several, pipelines=(Cancel,))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run.run(), 1)
    assert run.stats.reason == 'cancelled'
    assert run.stats.requests == 1
    assert run.stats.errors == 0
    assert run.stats.finished_at is not None


async def test_cancelling_during_open_closes_prior_component():
    opening = asyncio.Event()
    events = []

    class First:
        def process_item(self, item):
            return item

        def close_crawler(self):
            events.append('first closed')

    class Second(First):
        async def open_crawler(self):
            opening.set()
            await asyncio.Event().wait()

    run = run_for(pipelines=(First, Second))
    task = asyncio.create_task(run.run())
    await asyncio.wait_for(opening.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ['first closed']
    assert run.stats.reason == 'cancelled'


async def test_many_isolates_write_failures_and_component_instances(monkeypatch):
    instances = []

    class PerRun:
        @classmethod
        def from_crawler(cls, crawler):
            result = cls()
            result.fail = crawler.name == 'bad'
            instances.append(result)
            return result

        def process_item(self, item):
            if self.fail:
                raise OSError('storage down')
            return item

    class Bad(One):
        name = 'bad'
        settings = Settings(pipelines=(PerRun,), max_errors=None)

    class Good(Bad):
        name = 'good'

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())
    outcomes = [outcome async for outcome in crawl_many([Bad, Good, Good])]
    assert sum(isinstance(outcome.error, OSError) for outcome in outcomes) == 1
    assert sum(outcome.error is None for outcome in outcomes) == 2
    assert len({id(instance) for instance in instances}) == 3
    assert all(outcome.crawl is not None for outcome in outcomes)


def test_collect_receives_none_from_pipeline(monkeypatch):
    class Nothing:
        def process_item(self, item):
            return None

    class Configured(One):
        settings = Settings(pipelines=(Nothing,))

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())
    assert collect(Configured) == [None]


@pytest.mark.parametrize('consumer_fails', [False, True])
async def test_consumer_exit_closes_pipeline_before_http(monkeypatch, consumer_fails):
    events = []

    class Http(SessionHttp):
        async def __aexit__(self, *args):
            events.append('http close')

    class Track:
        def process_item(self, item):
            return item

        def close_crawler(self):
            events.append('pipeline close')

    class Endless(One):
        settings = Settings(pipelines=(Track,))

        async def parse(self, response):
            yield 1
            yield self.request(response.request.url, dont_filter=True)

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: Http())
    primary = ValueError('consumer')

    async def consume():
        async with open_crawl(Endless) as run:
            async for _ in run.stream():
                if consumer_fails:
                    raise primary
                break
        return run

    if consumer_fails:
        with pytest.raises(CrawlError) as caught:
            await consume()
        assert caught.value.__cause__ is primary
    else:
        assert (await consume()).stats.reason == 'cancelled'
    assert events == ['pipeline close', 'http close']


@pytest.mark.parametrize('consumer_fails', [False, True])
async def test_finished_background_failure_is_observed_on_context_exit(monkeypatch, consumer_fails):
    write_error = OSError('late close')
    consumer_error = ValueError('consumer')

    class LateFailure:
        def process_item(self, item):
            return item

        def close_crawler(self):
            raise write_error

    class Configured(One):
        settings = Settings(pipelines=(LateFailure,))

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())
    with pytest.raises(CrawlError) as caught:
        async with open_crawl(Configured) as run:
            async for _ in run.stream():
                # Finish the engine without asking the stream for the next item.
                await asyncio.wait({run._run_task}, timeout=2)
                assert run._run_task.done()
                if consumer_fails:
                    raise consumer_error
                break
    assert caught.value.__cause__ is (consumer_error if consumer_fails else write_error)
    if consumer_fails:
        assert 'late close' in str(consumer_error.__notes__)


async def test_cancelling_context_exit_is_not_swallowed(monkeypatch):
    closing = asyncio.Event()
    release = asyncio.Event()
    closed = []

    class SlowClose:
        def process_item(self, item):
            return item

        async def close_crawler(self):
            closing.set()
            await release.wait()
            closed.append(True)

    class Endless(One):
        settings = Settings(pipelines=(SlowClose,))

        async def parse(self, response):
            while True:
                yield 1

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())

    async def leave():
        async with open_crawl(Endless) as run:
            async for _ in run.stream():
                break

    task = asyncio.create_task(leave())
    await asyncio.wait_for(closing.wait(), 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert closed == [True]


@pytest.mark.parametrize('phase', ['opened', 'start_requests'])
async def test_crawler_setup_error_still_closes_pipeline(phase):
    events = []
    failure = ValueError('crawler setup')

    class Resource:
        def process_item(self, item):
            return item

        def close_crawler(self):
            events.append('pipeline closed')

    class Broken(One):
        async def opened(self):
            if phase == 'opened':
                raise failure

        async def start_requests(self):
            raise failure
            yield

        async def closed(self, stats):
            events.append('crawler closed')

    with pytest.raises(ValueError) as caught:
        await run_for(Broken, pipelines=(Resource,)).run()
    assert caught.value is failure
    assert events == (
        ['pipeline closed'] if phase == 'opened' else ['crawler closed', 'pipeline closed']
    )


async def test_unused_context_opens_no_pipeline_resources(monkeypatch):
    events = []

    class Resource:
        def __init__(self):
            events.append('created')

        def process_item(self, item):
            return item

    class Configured(One):
        settings = Settings(pipelines=(Resource,))

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())
    async with open_crawl(Configured):
        pass
    assert events == []


@pytest.mark.parametrize('phase', ['crawler', 'pipeline'])
async def test_cancellation_from_close_does_not_skip_other_resources(phase):
    events = []

    class First:
        def process_item(self, item):
            return item

        def close_crawler(self):
            events.append('first')

    class Second(First):
        def close_crawler(self):
            events.append('second')
            if phase == 'pipeline':
                raise asyncio.CancelledError()

    class Closing(One):
        async def closed(self, stats):
            if phase == 'crawler':
                raise asyncio.CancelledError()

    run = run_for(Closing, pipelines=(First, Second))
    with pytest.raises(asyncio.CancelledError):
        await run.run()
    assert events == ['second', 'first']
    assert run.stats.reason == 'cancelled'
