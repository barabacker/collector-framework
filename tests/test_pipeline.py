"""Pipeline components own their state and resources for one crawl."""

import asyncio
from dataclasses import replace

import pytest
from tests.conftest import FakeHttp

from collector import Crawler, CrawlerContext, Settings
from collector.engine.pipeline import PipelineManager


class One(Crawler):
    name = 'one'

    async def parse(self, response):
        yield 1


def crawler(*pipelines, **options):
    instance = One(CrawlerContext(http=FakeHttp()))
    instance.settings = replace(instance.settings, pipelines=pipelines, pipeline_options=options)
    return instance


async def test_chain_creates_components_from_crawler_and_closes_in_reverse():
    events = []

    class Add:
        @classmethod
        def from_crawler(cls, instance):
            assert instance.name == 'one'
            result = cls()
            result.value = instance.settings.pipeline_options['add']
            return result

        def open_crawler(self):
            events.append('open add')

        def process_item(self, item):
            events.append('add')
            return item + self.value

        async def close_crawler(self):
            events.append('close add')

    class Double:
        async def open_crawler(self):
            events.append('open double')

        async def process_item(self, item):
            events.append('double')
            return item * 2

        def close_crawler(self):
            events.append('close double')

    manager = PipelineManager(crawler(Add, Double, add=1))
    await manager.open()
    assert await manager.process(1) == 4
    assert await manager.close() == []
    assert await manager.close() == []
    assert events == ['open add', 'open double', 'add', 'double', 'close double', 'close add']


async def test_each_crawl_has_a_separate_component_instance():
    class Counter:
        def __init__(self):
            self.count = 0

        def process_item(self, item):
            self.count += 1
            return self.count

    first = PipelineManager(crawler(Counter))
    second = PipelineManager(crawler(Counter))
    await first.open()
    await second.open()
    assert await first.process(None) == 1
    assert await first.process(None) == 2
    assert await second.process(None) == 1


async def test_none_is_a_valid_result_and_lifecycle_methods_are_optional():
    class Nothing:
        def process_item(self, item):
            return None

    manager = PipelineManager(crawler(Nothing))
    await manager.open()
    assert await manager.process(1) is None
    assert await manager.close() == []


@pytest.mark.parametrize('invalid', [object, object()])
async def test_invalid_component_is_rejected_before_opening_resources(invalid):
    opened = []

    class Valid:
        def open_crawler(self):
            opened.append(True)

        def process_item(self, item):
            return item

    manager = PipelineManager(crawler(Valid, invalid))
    with pytest.raises(TypeError, match='pipeline'):
        await manager.open()
    assert opened == []


@pytest.mark.parametrize('cancel', [False, True])
async def test_partial_open_closes_only_successfully_opened_components(cancel):
    events = []
    failure = asyncio.CancelledError() if cancel else ValueError('open')

    class First:
        def process_item(self, item):
            return item

        def close_crawler(self):
            events.append('first')

    class Second(First):
        async def open_crawler(self):
            raise failure

        def close_crawler(self):
            events.append('second')

    manager = PipelineManager(crawler(First, Second))
    with pytest.raises(type(failure)) as caught:
        await manager.open()
    assert caught.value is failure
    assert await manager.close() == []
    assert events == ['first']


async def test_all_close_methods_run_even_if_several_fail():
    failures = [ValueError('first'), RuntimeError('second')]

    class First:
        def process_item(self, item):
            return item

        def close_crawler(self):
            raise failures[0]

    class Second(First):
        def close_crawler(self):
            raise failures[1]

    manager = PipelineManager(crawler(First, Second))
    await manager.open()
    assert await manager.close() == [failures[1], failures[0]]


def test_pipeline_options_are_not_shared_between_settings_instances():
    first, second = Settings(), Settings()
    first.pipeline_options['value'] = 1
    assert second.pipeline_options == {}
