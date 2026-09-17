"""run_parser / crawl: build the client, run the crawl, hand back the parser."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import BaseParser, collect, crawl, open_crawler, run_parser

URL = 'https://example.test/'


class _Counting(BaseParser):
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
    monkeypatch.setattr('collector.runner.build_http_client', lambda parser_cls: http)
    return http


def test_run_parser_returns_the_crawler(monkeypatch):
    _patch_client(monkeypatch)
    sink: list[Any] = []

    crawler = run_parser(_Counting, sink=sink)

    assert crawler.stats.items == 1
    # The parser instance comes back on the crawler, for an app's own counters.
    assert crawler.parser.saved == [{'url': URL}]
    assert sink == [{'url': URL}]


def test_run_parser_passes_params_through(monkeypatch):
    _patch_client(monkeypatch)
    crawler = run_parser(_Counting, params={'max_pages': '2'}, sink=[])
    assert crawler.parser.ctx.params == {'max_pages': '2'}


def test_run_parser_logs_through_the_default_logger(monkeypatch, caplog):
    _patch_client(monkeypatch)

    class _Logging(_Counting):
        name = 'logging'

        async def parse(self, response: Any):
            await self.log('hello')
            yield {'url': response.request.url}

    with caplog.at_level('INFO', logger='collector.runner'):
        run_parser(_Logging, sink=[])

    assert '[logging] hello' in caplog.text


async def test_crawl_is_the_async_entry_point(monkeypatch):
    _patch_client(monkeypatch)
    crawler = await crawl(_Counting, sink=[])
    assert crawler.stats.items == 1


def test_collect_returns_the_items(monkeypatch):
    _patch_client(monkeypatch)

    class _Plain(BaseParser):
        name = 'plain'
        start_urls = [URL]

        async def parse(self, response: Any):
            yield {'url': response.request.url}
            yield {'url': response.request.url + '#2'}

    assert collect(_Plain) == [{'url': URL}, {'url': URL + '#2'}]


def test_collect_keeps_the_parsers_own_process_item(monkeypatch):
    """collect() drains stream(), so the parser's own hook still runs."""
    _patch_client(monkeypatch)
    tagged: list[Any] = []

    class _Tagging(BaseParser):
        name = 'tagging'
        start_urls = [URL]

        async def parse(self, response: Any):
            yield {'url': response.request.url}

        async def process_item(self, item: Any) -> None:
            await super().process_item(item)
            tagged.append(item)

    assert collect(_Tagging) == [{'url': URL}]
    assert tagged == [{'url': URL}]


def test_collect_does_not_substitute_the_parser_class(monkeypatch):
    """collect() used to run a dynamic subclass; the class it is given now runs as is."""
    _patch_client(monkeypatch)
    ran: list[type] = []

    class _Plain(BaseParser):
        name = 'plain'
        start_urls = [URL]

        async def parse(self, response: Any):
            ran.append(type(self))
            yield {'ok': True}

    assert collect(_Plain) == [{'ok': True}]
    assert ran == [_Plain]


class _AlwaysFails(BaseParser):
    """Three start URLs, every one of them blowing up in parse()."""

    name = 'always_fails'
    start_urls = [f'{URL}{n}' for n in range(3)]

    async def parse(self, response: Any):
        raise ValueError(f'boom {response.request.url}')
        yield  # pragma: no cover — makes parse() a generator


def test_a_failed_crawl_carries_its_crawler_out_on_the_exception(monkeypatch):
    """run() re-raises one error; the other two must not vanish with the frame."""
    _patch_client(monkeypatch)

    with pytest.raises(ValueError) as excinfo:
        run_parser(_AlwaysFails)

    crawler = excinfo.value.crawler
    assert len(crawler.errors) == 3
    assert crawler.stats.errors == 3
    assert {req.url for req, _ in crawler.errors} == set(_AlwaysFails.start_urls)
    assert any('3 requests failed' in note for note in excinfo.value.__notes__)


def test_the_failure_count_is_noted_once_not_once_per_entry_point(monkeypatch):
    """crawl() runs inside open_crawler(); only one of them may annotate."""
    _patch_client(monkeypatch)

    with pytest.raises(ValueError) as excinfo:
        run_parser(_AlwaysFails)

    notes = [note for note in excinfo.value.__notes__ if 'requests failed' in note]
    assert len(notes) == 1


def test_a_single_failure_is_not_annotated_with_a_count(monkeypatch):
    """One error is already the one being raised — a count would be noise."""
    _patch_client(monkeypatch)

    class _OneStart(_AlwaysFails):
        start_urls = [URL]

    with pytest.raises(ValueError) as excinfo:
        run_parser(_OneStart)

    assert len(excinfo.value.crawler.errors) == 1
    assert not any('requests failed' in note for note in getattr(excinfo.value, '__notes__', []))


async def test_open_crawler_streams_and_exposes_stats(monkeypatch):
    _patch_client(monkeypatch)

    class _Three(BaseParser):
        name = 'three'
        start_urls = [URL]

        async def parse(self, response: Any):
            for n in range(3):
                yield {'n': n}

    async with open_crawler(_Three) as crawler:
        got = [item async for item in crawler.stream()]
        assert crawler.stats.items == 3

    assert got == [{'n': 0}, {'n': 1}, {'n': 2}]


async def test_open_crawler_stops_a_crawl_a_consumer_walked_away_from(monkeypatch):
    """The session must not close while workers are still using it."""
    http = _patch_client(monkeypatch)

    class _Endless(BaseParser):
        name = 'endless_runner'
        start_urls = [URL]

        async def parse(self, response: Any):
            yield {'tick': True}
            yield self.request(URL)

    async with open_crawler(_Endless) as crawler:
        async for _item in crawler.stream():
            break  # no aclosing() — open_crawler has to catch this itself
        run_task = crawler._run_task

    assert run_task is not None and run_task.cancelled()
    assert crawler._run_task is None
    calls_at_exit = len(http.calls)
    await asyncio.sleep(0.01)
    # Nothing kept crawling behind the closed session.
    assert len(http.calls) == calls_at_exit


async def test_open_crawler_attaches_the_crawler_to_a_failure(monkeypatch):
    _patch_client(monkeypatch)

    with pytest.raises(ValueError) as excinfo:
        async with open_crawler(_AlwaysFails) as crawler:
            await crawler.run()

    assert excinfo.value.crawler is crawler
    assert len(crawler.errors) == 3
