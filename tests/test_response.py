"""Response wraps a raw client response and exposes a parsel Selector."""

from __future__ import annotations

from tests.conftest import FakeResponse

from collector import Request, Response

HTML = '<html><body><h1>Лот 42</h1><a href="/next">next</a></body></html>'
HTML_RESPONSE = FakeResponse(text=HTML)


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
    response = Response(HTML_RESPONSE, Request(url='https://example.test/a/page'))
    href = response.selector().css('a::attr(href)').get()

    req = response.follow(href, metadata={'page': 2})

    assert req.url == 'https://example.test/next'
    assert req.metadata == {'page': 2}
    # No callback means parse(), the same default the parser's own request gets.
    assert req.callback is None


def test_follow_accepts_a_method_and_body():
    response = Response(HTML_RESPONSE, Request(url='https://example.test/'))
    req = response.follow('/search', method='POST', data={'q': 'лот'})
    assert (req.method, req.data) == ('POST', {'q': 'лот'})


def test_follow_carries_params_json_and_cookies():
    response = Response(HTML_RESPONSE, Request(url='https://example.test/'))
    req = response.follow('/api', method='POST', params={'page': 3}, json={'q': 'лот'})

    assert req.url == 'https://example.test/api'
    assert req.http_kwargs() == {'params': {'page': 3}, 'json': {'q': 'лот'}}
