"""Removal is explicit; storage effects remain possible in application pipelines."""

import pytest
from tests.conftest import SessionHttp

import collector
from collector import Crawler, Settings, collect, crawl, crawl_many, open_crawl, run_crawler


class One(Crawler):
    name = 'one'
    start_urls = ['https://example.test/']

    async def parse(self, response):
        yield {'number': 1}


@pytest.mark.parametrize('entrypoint', [open_crawl, crawl, crawl_many])
async def test_async_entrypoints_reject_removed_dataset_before_http(monkeypatch, entrypoint):
    built = []
    monkeypatch.setattr(
        'collector.engine.runner.build_http_client', lambda *a, **kw: built.append(1)
    )
    with pytest.raises(TypeError, match='dataset'):
        if entrypoint is open_crawl:
            async with entrypoint(One, dataset=object()):
                pass
        elif entrypoint is crawl_many:
            async for _ in entrypoint([One], dataset=object()):
                pass
        else:
            await entrypoint(One, dataset=object())
    assert built == []


@pytest.mark.parametrize('entrypoint', [collect, run_crawler])
def test_sync_entrypoints_reject_removed_dataset_before_http(monkeypatch, entrypoint):
    built = []
    monkeypatch.setattr(
        'collector.engine.runner.build_http_client', lambda *a, **kw: built.append(1)
    )
    with pytest.raises(TypeError, match='dataset'):
        entrypoint(One, dataset=object())
    assert built == []


def test_removed_storage_types_are_not_root_exports():
    assert not any(
        hasattr(collector, name) for name in ('Dataset', 'MemoryDataset', 'SqliteDataset')
    )


async def test_application_pipeline_saves_every_item_and_tags_its_crawler(monkeypatch):
    saved = []

    class Save:
        @classmethod
        def from_crawler(cls, crawler):
            component = cls()
            component.name = crawler.name
            return component

        async def process_item(self, item):
            saved.append((self.name, item.copy()))
            return item

    class First(One):
        name = 'first'
        settings = Settings(pipelines=(Save,))

    class Second(First):
        name = 'second'

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())

    async def consume(run):
        assert [item async for item in run.stream()] == [{'number': 1}]

    outcomes = [outcome async for outcome in crawl_many([First, Second], consume=consume)]
    assert all(outcome.error is None for outcome in outcomes)
    assert sorted(saved) == [('first', {'number': 1}), ('second', {'number': 1})]
