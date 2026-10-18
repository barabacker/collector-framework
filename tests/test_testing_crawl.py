from dataclasses import replace

import pytest

from collector import Crawl, Crawler, Settings
from collector.testing import FakeHttp, make_crawler


def crawler_type(events):
    class Save:
        def open_crawler(self):
            events.append('open')

        def process_item(self, item):
            events.append(item)
            return item

        def close_crawler(self):
            events.append('close')

    class Pages(Crawler):
        name = 'pages'
        start_urls = ['https://example.test/']
        settings = Settings(pipelines=(Save,), max_errors=1)

        async def parse(self, response):
            yield response.text
            if response.text == 'first':
                yield response.follow('/next')

    return Pages


async def test_full_crawl_runs_pipeline_and_leaves_http_to_owner():
    events = []
    async with FakeHttp(
        {('GET', 'https://example.test/'): 'first', ('GET', 'https://example.test/next'): 'second'}
    ) as http:
        run = Crawl(make_crawler(crawler_type(events), http=http))
        stats = await run.run()
        assert stats is run.stats
        assert stats.items == 2
        assert stats.errors == 0
        assert stats.reason == 'done'
        assert stats.finished_at is not None
        assert events == ['open', 'first', 'second', 'close']
        assert http.calls == [
            ('GET', 'https://example.test/'),
            ('GET', 'https://example.test/next'),
        ]
        assert not http.closed
    assert http.closed


@pytest.mark.parametrize('limit', [0, 1])
async def test_missing_route_uses_engine_error_policy_and_closes_pipeline(limit):
    events = []
    http = FakeHttp({('GET', 'https://example.test/'): 'first'})
    run = Crawl(make_crawler(crawler_type(events), http=http))
    run.crawler.settings = replace(run.crawler.settings, max_errors=limit)
    if limit == 0:
        with pytest.raises(LookupError, match='GET https://example.test/next'):
            await run.run()
    else:
        await run.run()
    assert run.stats.errors == 1
    assert run.stats.reason == ('max_errors' if limit == 0 else 'done')
    assert run.stats.finished_at is not None
    assert events == ['open', 'first', 'close']
    assert not http.closed
