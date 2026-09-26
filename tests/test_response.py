"""Response wraps a raw client response and exposes a parsel Selector."""

from __future__ import annotations

from typing import Any

from tests.conftest import FakeHttp, FakeResponse

from collector import Crawler, CrawlerContext, Request, Response

HTML = '<html><body><h1>Лот 42</h1><a href="/next">next</a></body></html>'
HTML_RESPONSE = FakeResponse(text=HTML)


class _Following(Crawler):
    """A minimal crawler, only so ``follow()`` has one to delegate to."""

    name = 'following'

    async def parse(self, response: Any):
        yield {}


def _crawler() -> _Following:
    return _Following(CrawlerContext(http=FakeHttp()))


def test_status_text_and_metadata_come_from_raw_and_request():
    req = Request(url='https://example.test/', metadata={'page': 2})
    response = Response(FakeResponse(text=HTML, status_code=201), req)

    assert response.status == 201
    assert response.text == HTML
    assert response.metadata == {'page': 2}
    assert response.request is req


def test_selector_queries_the_body():
    response = Response(FakeResponse(text=HTML), Request(url='https://example.test/'))
    assert response.selector().css('h1::text').get() == 'Лот 42'


def test_the_selector_is_built_once_and_reused():
    """A page is queried twice — items, then the next link — on one parse of it."""
    response = Response(FakeResponse(text=HTML), Request(url='https://example.test/'))
    assert response.selector() is response.selector()


def test_headers_come_from_the_raw_response():
    raw = FakeResponse(text=HTML, headers={'Retry-After': '2'})
    response = Response(raw, Request(url='https://example.test/'))
    assert response.headers == {'Retry-After': '2'}


def test_raw_exposes_the_underlying_response():
    raw = FakeResponse(text=HTML)
    assert Response(raw, Request(url='https://example.test/')).raw is raw


def test_json_parses_the_body():
    response = Response(FakeResponse(text='{"lots": [1, 2]}'), Request(url='https://example.test/'))
    assert response.json() == {'lots': [1, 2]}


def test_urljoin_resolves_against_the_page_url():
    response = Response(HTML_RESPONSE, Request(url='https://example.test/a/b/page'))
    assert response.urljoin('/next') == 'https://example.test/next'
    assert response.urljoin('next') == 'https://example.test/a/b/next'
    assert response.urljoin('https://other.test/x') == 'https://other.test/x'


def test_follow_builds_a_request_with_an_absolute_url():
    crawler = _crawler()
    response = Response(HTML_RESPONSE, Request(url='https://example.test/a/page'), crawler)
    href = response.selector().css('a::attr(href)').get()

    req = response.follow(href, metadata={'page': 2})

    assert req.url == 'https://example.test/next'
    assert req.metadata == {'page': 2}
    # No explicit callback means whatever crawler.request() defaults it to —
    # the same default a request built by the crawler itself gets, because
    # follow() forwards to it rather than building its own Request.
    assert req.callback == crawler.parse


def test_follow_accepts_a_method_and_body():
    crawler = _crawler()
    response = Response(HTML_RESPONSE, Request(url='https://example.test/'), crawler)
    req = response.follow('/search', method='POST', data={'q': 'лот'})
    assert (req.method, req.data) == ('POST', {'q': 'лот'})


def test_follow_carries_params_json_and_cookies():
    crawler = _crawler()
    response = Response(HTML_RESPONSE, Request(url='https://example.test/'), crawler)
    req = response.follow('/api', method='POST', params={'page': 3}, json={'q': 'лот'})

    assert req.url == 'https://example.test/api'
    assert req.http_kwargs() == {'params': {'page': 3}, 'json': {'q': 'лот'}}


FORM_PAGE = (
    '<form method="post" action="/list/">'
    '<input type="hidden" name="__VIEWSTATE" value="abc">'
    '<input type="submit" name="go" value="Искать">'
    '</form>'
)


def test_form_request_posts_the_form_through_the_crawler():
    crawler = _crawler()
    response = Response(
        FakeResponse(text=FORM_PAGE), Request(url='https://example.test/list/?page=1'), crawler
    )

    req = response.form_request(
        formdata={'__EVENTTARGET': 'pager$2'},
        click='go',
        metadata={'page': 2},
        headers={'Referer': 'https://example.test/list/'},
    )

    assert (req.method, req.url) == ('POST', 'https://example.test/list/')
    assert req.http_kwargs() == {
        'headers': {'Referer': 'https://example.test/list/'},
        'data': [('__VIEWSTATE', 'abc'), ('__EVENTTARGET', 'pager$2'), ('go', 'Искать')],
    }
    assert req.metadata == {'page': 2}
    # Built by crawler.request(), so a bare callback means parse() — as with follow().
    assert req.callback == crawler.parse


def test_a_get_form_sends_its_fields_as_params():
    crawler = _crawler()
    page = FakeResponse(text='<form action="/search"><input name="q" value="лот"></form>')
    response = Response(page, Request(url='https://example.test/list/'), crawler)

    req = response.form_request()

    assert (req.method, req.url) == ('GET', 'https://example.test/search')
    assert req.http_kwargs() == {'params': [('q', 'лот')]}


def test_follow_and_form_request_forward_the_de_duplication_fields():
    crawler = _crawler()
    page = FakeResponse(text='<form method="post"><input name="q" value="x"></form>')
    response = Response(page, Request(url='https://example.test/'), crawler)

    followed = response.follow('/next', unique_key='next', dont_filter=True)
    posted = response.form_request(unique_key='form', dont_filter=True)

    assert (followed.unique_key, followed.dont_filter) == ('next', True)
    assert (posted.unique_key, posted.dont_filter) == ('form', True)
