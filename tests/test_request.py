"""Request carries the per-request half of the transport; session-wide is Settings."""

from __future__ import annotations

from collector import Request


def test_an_untouched_request_sends_nothing_of_its_own():
    """A bare GET must not smuggle empty headers or a null body to the client."""
    assert Request(url='https://example.test/').http_kwargs() == {}


def test_set_fields_reach_the_transport():
    req = Request(
        url='https://example.test/',
        method='POST',
        headers={'X-Requested-With': 'XMLHttpRequest'},
        params={'page': 2},
        json={'lot': 42},
        cookies={'session': 'abc'},
    )

    assert req.http_kwargs() == {
        'headers': {'X-Requested-With': 'XMLHttpRequest'},
        'params': {'page': 2},
        'json': {'lot': 42},
        'cookies': {'session': 'abc'},
    }


def test_a_falsy_value_is_still_sent():
    """``None`` means "unset"; an empty dict is a choice the caller made."""
    assert Request(url='https://example.test/', params={}).http_kwargs() == {'params': {}}


def test_data_and_json_are_independent_fields():
    req = Request(url='https://example.test/', data='raw=1')
    assert req.http_kwargs() == {'data': 'raw=1'}
    assert req.json is None


def test_a_list_of_pairs_reaches_the_transport_as_is():
    """A form can repeat a name — a dict would keep only the last value."""
    pairs = [('lot', '1'), ('lot', '2')]
    req = Request(url='https://example.test/', method='POST', data=pairs, params=[('p', 1)])

    assert req.http_kwargs() == {'params': [('p', 1)], 'data': pairs}
