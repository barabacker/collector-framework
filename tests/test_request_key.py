"""The key a crawl de-duplicates on: which requests count as the same one."""

from __future__ import annotations

from typing import Any

from collector import Request
from collector.crawler import request_key

URL = 'https://example.test/lots'


def key(url: str = URL, **kwargs: Any) -> str:
    return request_key(Request(url=url, **kwargs))


def test_a_request_without_a_body_is_its_method_and_url():
    assert key() == 'GET|https://example.test/lots|'


def test_query_order_does_not_matter():
    assert key(f'{URL}?a=1&b=2') == key(f'{URL}?b=2&a=1')


def test_params_merge_into_the_query():
    assert key(f'{URL}?a=1', params={'b': 2}) == key(f'{URL}?b=2&a=1')
    assert key(f'{URL}?a=1', params=[('b', 2)]) == key(f'{URL}?b=2&a=1')


def test_the_fragment_is_dropped():
    assert key(f'{URL}#top') == key(URL)


def test_the_scheme_and_hosts_case_do_not_matter():
    assert key('HTTPS://Example.TEST/lots') == key(URL)


def test_the_path_is_kept_as_it_is():
    # Either can name a different page; the key does not rewrite what a URL means.
    assert key(f'{URL}/') != key(URL)
    assert key('https://example.test/LOTS') != key(URL)


def test_the_method_matters():
    assert key(method='POST') != key(method='GET')


def test_the_same_form_in_another_order_is_the_same_body():
    assert key(method='POST', data={'a': '1', 'b': '2'}) == key(
        method='POST', data={'b': '2', 'a': '1'}
    )


def test_a_different_body_is_a_different_request():
    # ASP.NET pagination: every page is a POST to the same URL.
    page_2 = key(method='POST', data={'__EVENTTARGET': 'pager$2'})
    page_3 = key(method='POST', data={'__EVENTTARGET': 'pager$3'})
    assert page_2 != page_3


def test_a_list_of_pairs_and_json_are_part_of_the_key():
    assert key(method='POST', data=[('a', '1')]) != key(method='POST', data=[('a', '2')])
    assert key(method='POST', json={'a': 1, 'b': 2}) == key(method='POST', json={'b': 2, 'a': 1})
    assert key(method='POST', json={'a': 1}) != key(method='POST', json={'a': 2})


def test_unique_key_replaces_the_computed_key():
    assert key(unique_key='lot-42') == 'lot-42'


def test_headers_and_cookies_do_not_change_the_key():
    assert key(headers={'X-Trace': '1'}, cookies={'session': 'abc'}) == key()


def test_the_new_fields_do_not_reach_the_transport():
    req = Request(url=URL, unique_key='lot-42', dont_filter=True)
    assert req.http_kwargs() == {}


def test_a_query_escaped_in_a_legacy_encoding_keeps_its_bytes():
    # Two different cp1251 searches must not collapse into one mangled key.
    assert key(f'{URL}?q=%EB%EE%F2') != key(f'{URL}?q=%E0%E1%E2')
    assert '%EB%EE%F2' in key(f'{URL}?q=%EB%EE%F2')


def test_a_list_in_params_is_the_name_repeated():
    assert key(URL, params={'a': [1, 2]}) == key(f'{URL}?a=1&a=2')


def test_only_the_host_is_lower_cased_not_the_credentials():
    assert key('HTTP://User:PaSS@Host:8080/p') == 'GET|http://User:PaSS@host:8080/p|'


def test_a_bytes_body_is_hashed_rather_than_rejected():
    assert key(method='POST', data=b'a=1') != key(method='POST', data=b'a=2')
