"""run_crawler / crawl: build the client, run the crawl, hand back the crawler."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import Crawler, CrawlError, collect, crawl, open_crawl, run_crawler

URL = 'https://example.test/'


class _Counting(Crawler):
    name = 'counting'
    start_urls = [URL]

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.saved: list[Any] = []

    async def parse(self, response: Any):
        yield {'url': response.request.url}

    async def process_item(self, item: Any) -> None:
        await super().process_item(item)
        self.ctx.sink.append(item)
        self.saved.append(item)


def _patch_client(monkeypatch) -> FakeHttp:
    """Replace the real client factory with a FakeHttp that closes cleanly."""
    http = FakeHttp()

    async def _aenter(self):
        return self

    async def _aexit(self, *exc_info):
        return None

    type(http).__aenter__ = _aenter
    type(http).__aexit__ = _aexit
    monkeypatch.setattr(
        'collector.engine.runner.build_http_client', lambda crawler_cls, **kwargs: http
    )
    return http


def test_run_crawler_returns_the_crawl(monkeypatch):
    _patch_client(monkeypatch)
    sink: list[Any] = []

    crawl = run_crawler(_Counting, sink=sink)

    assert crawl.stats.items == 1
    # The crawler instance comes back on the crawl, for an app's own counters.
    assert crawl.crawler.saved == [{'url': URL}]
    assert sink == [{'url': URL}]


def test_run_crawler_passes_params_through(monkeypatch):
    _patch_client(monkeypatch)
    crawl = run_crawler(_Counting, params={'max_pages': '2'}, sink=[])
    assert crawl.crawler.ctx.params == {'max_pages': '2'}


def test_run_crawler_logs_through_the_default_logger(monkeypatch, caplog):
    _patch_client(monkeypatch)

    class _Logging(_Counting):
        name = 'logging'

        async def parse(self, response: Any):
            await self.log('hello')
            yield {'url': response.request.url}

    with caplog.at_level('INFO', logger='collector.engine.runner'):
        run_crawler(_Logging, sink=[])

    assert '[logging] hello' in caplog.text


async def test_crawl_is_the_async_entry_point(monkeypatch):
    _patch_client(monkeypatch)
    run = await crawl(_Counting, sink=[])
    assert run.stats.items == 1


def test_collect_returns_the_items(monkeypatch):
    _patch_client(monkeypatch)

    class _Plain(Crawler):
        name = 'plain'
        start_urls = [URL]

        async def parse(self, response: Any):
            yield {'url': response.request.url}
            yield {'url': response.request.url + '#2'}

    assert collect(_Plain) == [{'url': URL}, {'url': URL + '#2'}]


def test_collect_keeps_the_crawlers_own_process_item(monkeypatch):
    """collect() drains stream(), so the crawler's own hook still runs."""
    _patch_client(monkeypatch)
    tagged: list[Any] = []

    class _Tagging(Crawler):
        name = 'tagging'
        start_urls = [URL]

        async def parse(self, response: Any):
            yield {'url': response.request.url}

        async def process_item(self, item: Any) -> None:
            await super().process_item(item)
            tagged.append(item)

    assert collect(_Tagging) == [{'url': URL}]
    assert tagged == [{'url': URL}]


def test_collect_does_not_substitute_the_crawler_class(monkeypatch):
    """collect() used to run a dynamic subclass; the class it is given now runs as is."""
    _patch_client(monkeypatch)
    ran: list[type] = []

    class _Plain(Crawler):
        name = 'plain'
        start_urls = [URL]

        async def parse(self, response: Any):
            ran.append(type(self))
            yield {'ok': True}

    assert collect(_Plain) == [{'ok': True}]
    assert ran == [_Plain]


class _AlwaysFails(Crawler):
    """Three start URLs, every one of them blowing up in parse()."""

    name = 'always_fails'
    start_urls = [f'{URL}{n}' for n in range(3)]

    async def parse(self, response: Any):
        raise ValueError(f'boom {response.request.url}')
        yield  # pragma: no cover — makes parse() a generator


def test_a_failed_crawl_carries_its_crawl_out_on_the_exception(monkeypatch):
    """run() re-raises one error; the other two must not vanish with the frame."""
    _patch_client(monkeypatch)

    with pytest.raises(CrawlError) as excinfo:
        run_crawler(_AlwaysFails)

    crawl = excinfo.value.crawl
    assert len(crawl.errors) == 3
    assert crawl.stats.errors == 3
    assert {req.url for req, _ in crawl.errors} == set(_AlwaysFails.start_urls)
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert '3 requests failed' in str(excinfo.value)


def test_the_failure_is_wrapped_once_not_once_per_entry_point(monkeypatch):
    """crawl() runs inside open_crawl(); only the outermost frame may wrap."""
    _patch_client(monkeypatch)

    with pytest.raises(CrawlError) as excinfo:
        run_crawler(_AlwaysFails)

    assert isinstance(excinfo.value.__cause__, ValueError)
    assert not isinstance(excinfo.value.__cause__, CrawlError)


def test_a_single_failure_is_not_described_as_plural(monkeypatch):
    """One error is already the one being raised — a count would be noise."""
    _patch_client(monkeypatch)

    class _OneStart(_AlwaysFails):
        start_urls = [URL]

    with pytest.raises(CrawlError) as excinfo:
        run_crawler(_OneStart)

    assert len(excinfo.value.crawl.errors) == 1
    assert 'requests failed' not in str(excinfo.value)


async def test_open_crawl_streams_and_exposes_stats(monkeypatch):
    _patch_client(monkeypatch)

    class _Three(Crawler):
        name = 'three'
        start_urls = [URL]

        async def parse(self, response: Any):
            for n in range(3):
                yield {'n': n}

    async with open_crawl(_Three) as crawl:
        got = [item async for item in crawl.stream()]
        assert crawl.stats.items == 3

    assert got == [{'n': 0}, {'n': 1}, {'n': 2}]


async def test_open_crawl_stops_a_crawl_a_consumer_walked_away_from(monkeypatch):
    """The session must not close while workers are still using it."""
    http = _patch_client(monkeypatch)

    class _Endless(Crawler):
        name = 'endless_runner'
        start_urls = [URL]

        async def parse(self, response: Any):
            yield {'tick': True}
            # Deliberately unbounded — dedupe would otherwise drop this after
            # the first repeat and the crawl would stop being endless.
            yield self.request(URL, dont_filter=True)

    async with open_crawl(_Endless) as crawl:
        async for _item in crawl.stream():
            break  # no aclosing() — open_crawl has to catch this itself
        run_task = crawl._run_task

    assert run_task is not None and run_task.cancelled()
    assert crawl._run_task is None
    calls_at_exit = len(http.calls)
    await asyncio.sleep(0.01)
    # Nothing kept crawling behind the closed session.
    assert len(http.calls) == calls_at_exit


async def test_open_crawl_attaches_the_crawl_to_a_failure(monkeypatch):
    _patch_client(monkeypatch)

    with pytest.raises(CrawlError) as excinfo:
        async with open_crawl(_AlwaysFails) as crawl:
            await crawl.run()

    assert excinfo.value.crawl is crawl
    assert len(crawl.errors) == 3
