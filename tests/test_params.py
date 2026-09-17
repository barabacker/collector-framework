"""The two knobs the crawler honours, read out of strings from the outside world.

Forgiving on purpose: a bad value in a job payload falls back to the default
rather than killing the crawl, and the edge cases are what that promise is.
"""

from __future__ import annotations

import pytest

from collector.params import read_concurrency, read_max_requests


@pytest.mark.parametrize(
    ('params', 'expected'),
    [
        ({}, 4),
        ({'concurrency': ''}, 4),
        ({'concurrency': '8'}, 8),
        ({'concurrency': '0'}, 4),
        ({'concurrency': 'many'}, 4),
    ],
)
def test_read_concurrency_falls_back_to_the_default(params, expected):
    assert read_concurrency(params, 4) == expected


@pytest.mark.parametrize(
    ('params', 'default', 'expected'),
    [
        ({'max_requests': '500'}, None, 500),
        ({}, None, None),
        ({}, 100, 100),
        ({'max_requests': ''}, 100, 100),
        ({'max_requests': '0'}, 100, 100),
        ({'max_requests': '-5'}, 100, 100),
        ({'max_requests': 'lots'}, 100, 100),
        # A param must be able to lift a ceiling the parser set, not only lower it.
        ({'max_requests': '900'}, 100, 900),
    ],
)
def test_read_max_requests(params, default, expected):
    assert read_max_requests(params, default) == expected
