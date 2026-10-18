"""Exercise example persistence without contacting its sample site."""

import asyncio
import json
import threading

import pytest
from examples.storage import JsonLinesPipeline, _write
from tests.conftest import SessionHttp

from collector import Crawler, Settings, crawl


async def test_jsonl_example_appends_unicode_and_releases_file(monkeypatch, tmp_path):
    path = tmp_path / 'items.jsonl'

    class Items(Crawler):
        name = 'items'
        start_urls = ['https://example.test/']
        settings = Settings(pipelines=(JsonLinesPipeline,), pipeline_options={'jsonl_path': path})

        async def parse(self, response):
            yield {'text': 'Привет'}
            yield {'text': 'world'}

    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())
    for _ in range(2):
        assert (await crawl(Items)).stats.items == 2
    assert [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] == [
        {'text': 'Привет'},
        {'text': 'world'},
        {'text': 'Привет'},
        {'text': 'world'},
    ]
    path.rename(tmp_path / 'closed.jsonl')


async def test_example_waits_for_thread_write_when_cancelled():
    started = asyncio.Event()
    release = threading.Event()
    events = []
    loop = asyncio.get_running_loop()

    class File:
        def write(self, line):
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            events.append(line)

    task = asyncio.create_task(_write(File(), 'written'))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        # Let cancellation reach the shield while the real thread is blocked.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ['written']
