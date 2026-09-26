"""crawl_many: many crawlers at once, capped, each failure kept to itself."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import Crawl, Crawler, Outcome, crawl_many


class _Http(FakeHttp):
    """FakeHttp that can stand where open_crawl() expects a session."""

    async def __aenter__(self) -> _Http:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


def _patch_client(monkeypatch, fail_for: set[type[Crawler]] | None = None) -> list[type[Crawler]]:
    """Replace the client factory; record which crawler classes asked for one."""
    built: list[type[Crawler]] = []

    def factory(crawler_cls: type[Crawler], **kwargs: Any) -> _Http:
        built.append(crawler_cls)
        if fail_for and crawler_cls in fail_for:
            raise OSError('no route to host')
        return _Http()

    monkeypatch.setattr('collector.engine.runner.build_http_client', factory)
    return built


def _crawler(site: str, *, fail: bool = False) -> type[Crawler]:
    """A one-page crawler that logs, then emits one item — or raises."""

    class One(Crawler):
        name = site
        start_urls = [f'https://{site}.test/']

        async def parse(self, response: Any):
            await self.log(f'parsing {site}')
            if fail:
                raise RuntimeError(f'{site} broke')
            yield {'site': site}

    return One


async def drain(crawlers: list[type[Crawler]], **kwargs: Any) -> list[Outcome]:
    return [outcome async for outcome in crawl_many(crawlers, **kwargs)]


# ── running ──────────────────────────────────────────────────────────────────


async def test_outcomes_arrive_as_crawlers_finish(monkeypatch):
    _patch_client(monkeypatch)
    delays = {'slow': 0.05, 'fast': 0.0}

    async def consume(crawl: Crawl) -> None:
        await asyncio.sleep(delays[crawl.crawler.name])
        await crawl.run()

    outcomes = await drain([_crawler('slow'), _crawler('fast')], consume=consume)

    assert [outcome.crawler_cls.name for outcome in outcomes] == ['fast', 'slow']
    assert all(outcome.error is None for outcome in outcomes)
    assert all(outcome.crawl.stats.items == 1 for outcome in outcomes)
    assert all(outcome.elapsed >= 0 for outcome in outcomes)


async def test_no_more_than_concurrency_crawlers_run_at_once(monkeypatch):
    _patch_client(monkeypatch)
    running = peak = 0

    async def consume(crawl: Crawl) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        await crawl.run()
        running -= 1

    outcomes = await drain([_crawler(f's{i}') for i in range(5)], concurrency=2, consume=consume)

    assert len(outcomes) == 5
    assert peak == 2


KEPT: list[Any] = []


class _Keeping(Crawler):
    name = 'keeping'
    start_urls = ['https://keeping.test/']

    async def parse(self, response: Any):
        yield {'n': 1}

    async def process_item(self, item: Any) -> None:
        KEPT.append(item)


async def test_without_a_consumer_items_reach_process_item(monkeypatch):
    _patch_client(monkeypatch)
    KEPT.clear()

    await drain([_Keeping])

    assert KEPT == [{'n': 1}]


async def test_log_lines_carry_the_crawlers_name(monkeypatch):
    _patch_client(monkeypatch)
    lines: list[tuple[str, str]] = []

    async def log(name: str, message: str) -> None:
        lines.append((name, message))

    await drain([_crawler('a'), _crawler('b')], log=log)

    assert sorted(lines) == [('a', 'parsing a'), ('b', 'parsing b')]


async def test_without_a_log_lines_go_to_the_logger_tagged(monkeypatch, caplog):
    _patch_client(monkeypatch)
    caplog.set_level(logging.INFO, logger='collector.engine.many')

    await drain([_crawler('a')])

    assert '[a] parsing a' in caplog.text


async def test_a_concurrency_below_one_is_an_error(monkeypatch):
    _patch_client(monkeypatch)
    with pytest.raises(ValueError, match='concurrency'):
        await drain([_crawler('a')], concurrency=0)


# ── failures ─────────────────────────────────────────────────────────────────


async def test_a_failing_crawler_keeps_its_stats_and_spares_the_others(monkeypatch):
    _patch_client(monkeypatch)

    outcomes = {
        outcome.crawler_cls.name: outcome
        for outcome in await drain([_crawler('bad', fail=True), _crawler('good')])
    }

    bad = outcomes['bad']
    # The original failure, not the CrawlError wrapped around it.
    assert isinstance(bad.error, RuntimeError)
    assert str(bad.error) == 'bad broke'
    assert bad.crawl is not None
    assert bad.crawl.stats.errors == 1
    assert outcomes['good'].error is None


async def test_a_failing_consumer_is_isolated_too(monkeypatch):
    _patch_client(monkeypatch)

    async def consume(crawl: Crawl) -> None:
        if crawl.crawler.name == 'picky':
            raise KeyError('no room')
        await crawl.run()

    outcomes = {
        outcome.crawler_cls.name: outcome
        for outcome in await drain([_crawler('picky'), _crawler('easy')], consume=consume)
    }

    assert isinstance(outcomes['picky'].error, KeyError)
    assert outcomes['picky'].crawl is not None
    assert outcomes['easy'].error is None


async def test_a_crawler_whose_client_cannot_be_built_has_no_crawl(monkeypatch):
    down, up = _crawler('down'), _crawler('up')
    _patch_client(monkeypatch, fail_for={down})

    outcomes = {outcome.crawler_cls.name: outcome for outcome in await drain([down, up])}

    assert isinstance(outcomes['down'].error, OSError)
    assert outcomes['down'].crawl is None
    assert outcomes['up'].error is None


@dataclass(frozen=True)
class _Window:
    since: date | None = None


class _Dated(Crawler):
    name = 'dated'
    start_urls = ['https://dated.test/']
    params = _Window()

    async def parse(self, response: Any):
        yield {}


async def test_bad_params_for_one_crawler_stop_the_run_before_anything_is_built(monkeypatch):
    built = _patch_client(monkeypatch)

    with pytest.raises(ValueError, match=r"_Dated: params\['since'\]"):
        await drain([_crawler('free'), _Dated], params={'since': 'someday'})

    assert built == []


# ── leaving early ────────────────────────────────────────────────────────────


async def test_leaving_early_cancels_the_crawlers_still_running(monkeypatch):
    _patch_client(monkeypatch)
    reasons: dict[str, str] = {}

    class Slow(Crawler):
        name = 'slow'
        start_urls = ['https://slow.test/']

        async def parse(self, response: Any):
            await asyncio.sleep(10)
            yield {}

        async def closed(self, stats: Any) -> None:
            reasons['slow'] = stats.reason

    fast = _crawler('fast')

    async with contextlib.aclosing(crawl_many([Slow, fast])) as outcomes:
        async for outcome in outcomes:
            assert outcome.crawler_cls is fast
            break

    assert reasons == {'slow': 'cancelled'}
