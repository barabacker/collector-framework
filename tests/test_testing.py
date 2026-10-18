import asyncio
from dataclasses import dataclass

import pytest

from collector import Crawler
from collector.testing import collect_callback, make_crawler, make_response


@dataclass(frozen=True)
class Params:
    pages: int = 1


class Example(Crawler):
    name = 'example'
    params = Params()

    async def opened(self):
        raise AssertionError('lifecycle must not run')

    async def parse(self, response):
        yield response.follow('/next')
        yield {'text': response.selector().css('p::text').get()}
        yield None

    async def process_item(self, item):
        raise AssertionError('pipeline must not run')


async def test_constructor_preserves_inputs_and_validates_params():
    http = []  # Explicitly falsy, but still caller-owned.
    sink = object()

    async def log(message):
        pass

    crawler = make_crawler(Example, params={'pages': '2'}, http=http, sink=sink, log=log)
    assert type(crawler) is Example
    assert crawler.params.pages == 2
    assert crawler.http is http
    assert crawler.ctx.sink is sink
    assert crawler.ctx.log is log
    assert crawler.settings is Example.settings
    with pytest.raises(ValueError):
        make_crawler(Example, params={'pages': 'bad'})
    with pytest.raises(LookupError):
        await make_crawler(Example).http.request('GET', 'https://example.test/')


async def test_callback_uses_real_response_and_groups_results():
    crawler = make_crawler(Example)
    request = crawler.request('https://example.test/', metadata={'page': 1})
    response = make_response(
        '<p>hello</p>', request=request, crawler=crawler, status=201, headers={'X-Test': 'yes'}
    )
    assert response.request is request
    assert response.metadata is request.metadata
    assert response.status == 201
    assert response.headers['x-test'] == 'yes'
    assert response.raw.url == request.url
    assert response.raw.content == b'<p>hello</p>'
    requests, items = await collect_callback(crawler.parse(response))
    assert len(requests) == 1
    assert requests[0].url == 'https://example.test/next'
    assert items == [{'text': 'hello'}, None]
    assert make_response('{"ok": true}', request=request).json() == {'ok': True}
    form = make_response(
        '<form action="/save" method="post"><input name="x" value="y"></form>',
        request=request,
        crawler=crawler,
    ).form_request()
    assert form.url == 'https://example.test/save'
    assert form.method == 'POST'


@pytest.mark.parametrize('failure', [ValueError('callback'), asyncio.CancelledError()])
async def test_callback_errors_propagate(failure):
    async def results():
        yield None
        raise failure

    with pytest.raises(type(failure)) as caught:
        await collect_callback(results())
    assert caught.value is failure


async def test_collect_preserves_order_and_iterates_once():
    crawler = make_crawler(Example)
    first, second = crawler.request('first'), crawler.request('second')

    class Results:
        calls = 0

        def __aiter__(self):
            self.calls += 1
            assert self.calls == 1
            return self.values()

        async def values(self):
            for value in [first, 1, second, None, 2]:
                yield value

    assert await collect_callback(Results()) == ([first, second], [1, None, 2])
