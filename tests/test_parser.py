"""Parser is declarative: it builds requests and describes items, nothing more."""

from __future__ import annotations

from typing import Any

from tests.conftest import FakeHttp

from collector import Parser, ParserContext, Settings

PAGE_1 = 'https://example.test/p1'
PAGE_2 = 'https://example.test/p2'


class _TwoPages(Parser):
    name = 'two_pages'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        yield {'url': response.request.url}


def _parser() -> _TwoPages:
    return _TwoPages(ParserContext(http=FakeHttp()))


def test_a_parser_keeps_no_run_state():
    """Counters and failures belong to the Crawler; a parser is reusable and inert."""
    parser = _parser()
    assert not hasattr(parser, 'item_count')
    assert not hasattr(parser, 'errors')
    assert not hasattr(parser, 'crawl')


def test_the_context_carries_only_fields_that_have_a_reader():
    """A slot nothing reads is not a feature; it is public API to maintain."""
    from dataclasses import fields

    assert [f.name for f in fields(ParserContext)] == ['http', 'params', 'sink', 'log']


def test_request_defaults_its_callback_to_parse():
    parser = _parser()
    req = parser.request(PAGE_2)

    assert req.url == PAGE_2
    assert req.method == 'GET'
    assert req.callback == parser.parse
    assert req.metadata == {}


def test_request_carries_the_transport_fields():
    req = _parser().request(PAGE_2, method='POST', params={'page': 2}, json={'q': 1})
    assert req.method == 'POST'
    assert req.http_kwargs() == {'params': {'page': 2}, 'json': {'q': 1}}


async def test_start_requests_defaults_to_start_urls():
    reqs = [req async for req in _parser().start_requests()]
    assert [r.url for r in reqs] == [PAGE_1]


async def test_start_requests_can_be_overridden():
    class _PostStart(Parser):
        name = 'post_start'

        async def start_requests(self):
            yield self.request(PAGE_1, method='POST', data={'q': '1'}, metadata={'seed': True})

        async def parse(self, response: Any):
            yield {}

    parser = _PostStart(ParserContext(http=FakeHttp()))
    reqs = [req async for req in parser.start_requests()]

    assert [(r.method, r.url, r.data, r.metadata) for r in reqs] == [
        ('POST', PAGE_1, {'q': '1'}, {'seed': True})
    ]


async def test_log_is_a_noop_without_a_log_callable():
    await _parser().log('nothing blows up')


async def test_log_writes_through_the_context(ctx_factory):
    ctx, lines = ctx_factory(FakeHttp())
    await _TwoPages(ctx).log('visited')
    assert lines == ['visited']


async def test_process_item_is_a_noop_by_default():
    await _parser().process_item({'anything': 1})


def test_a_subclass_narrows_its_parents_settings():
    from dataclasses import replace

    class _Slow(_TwoPages):
        settings = replace(_TwoPages.settings, delay=1.5, max_requests=10)

    assert _Slow.settings.delay == 1.5
    assert _Slow.settings.max_requests == 10
    # Untouched fields still come from the parent.
    assert _Slow.settings.impersonate == Settings().impersonate
