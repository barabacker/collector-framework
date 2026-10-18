import pytest

from collector.testing import FakeHttp, FakeResponse


def test_response_content_and_headers():
    response = FakeResponse(text='я', headers={'Content-Type': 'text/plain'})
    assert response.content == 'я'.encode()
    assert response.headers['content-type'] == 'text/plain'
    assert FakeResponse(text='x', content=b'').content == b''
    assert FakeResponse().url == ''
    response.headers['new'] = 'value'
    assert 'new' not in FakeResponse().headers


async def test_routes_record_attempts_and_preserve_fixtures():
    fixture = FakeResponse(text='body', status_code=404)
    failure = OSError('offline')
    http = FakeHttp({('get', 'url'): fixture, ('POST', 'url'): 'posted', ('GET', 'error'): failure})
    nested = {'x': 1}
    first = await http.request('get', 'url', json=nested)
    first.headers['changed'] = 'yes'
    second = await http.request('GET', 'url')
    assert (first.text, first.status_code, first.url) == ('body', 404, 'url')
    assert fixture.url == ''
    assert 'changed' not in fixture.headers
    assert 'changed' not in second.headers
    assert (await http.request('POST', 'url')).text == 'posted'
    with pytest.raises(OSError) as caught:
        await http.request('GET', 'error')
    assert caught.value is failure
    with pytest.raises(LookupError, match='GET missing'):
        await http.request('GET', 'missing')
    assert http.calls == [
        ('GET', 'url'),
        ('GET', 'url'),
        ('POST', 'url'),
        ('GET', 'error'),
        ('GET', 'missing'),
    ]
    assert http.sent == [{'json': nested}, {}, {}, {}, {}]
    assert http.sent[0]['json'] is nested
    assert FakeHttp().calls == []


async def test_close_even_on_error():
    http = FakeHttp({('GET', 'url'): 'body'})
    with pytest.raises(ValueError):
        async with http as entered:
            assert entered is http
            assert not http.closed
            raise ValueError('consumer')
    assert http.closed
    await http.close()
    with pytest.raises(RuntimeError, match='closed'):
        await http.request('GET', 'url')


async def test_literal_urls_and_empty_routes():
    http = FakeHttp({('GET', 'url/'): ''})
    with pytest.raises(LookupError):
        await http.request('GET', 'url')
    with pytest.raises(LookupError):
        await FakeHttp().request('GET', 'https://example.test/')
