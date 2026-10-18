import asyncio

import pytest
from tests.conftest import FakeHttp

from collector import Crawl, Crawler, CrawlerContext


@pytest.mark.parametrize('cancel', [False, True])
async def test_start_failure_closes_opened_crawler(cancel):
    entered = asyncio.Event()
    closed = []
    failure = RuntimeError('start failed')

    class Starts(Crawler):
        name = 'starts'

        async def start_requests(self):
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise failure
            yield

        async def parse(self, response):
            yield None

        async def closed(self, stats):
            closed.append(stats)

    http = FakeHttp()
    run = Crawl(Starts(CrawlerContext(http=http)))
    task = asyncio.create_task(run.run())
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError) as caught:
        await task
    if not cancel:
        assert caught.value is failure
    else:
        assert run.stats.reason == 'cancelled'
    assert closed == [run.stats]
    assert run.stats.finished_at is not None
    assert http.calls == []
