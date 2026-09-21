"""The two knobs the crawler honours, read out of strings from the outside world.

Forgiving on purpose: a bad value in a job payload falls back to the default
rather than killing the crawl, and the edge cases are what that promise is.
"""

from __future__ import annotations

import pytest

from collector.engine.params import read_concurrency, read_max_requests, worker_count


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


@pytest.mark.parametrize(
    ('params', 'default', 'expected'),
    [
        ({}, 4, 4),
        ({'concurrency': '10'}, 4, 10),
        # A floor of one belongs to the question: zero workers means a queue
        # nobody drains, and the crawl waits on it for ever.
        ({}, 0, 1),
        ({}, -3, 1),
        ({'concurrency': '0'}, 0, 1),
        ({'concurrency': 'many'}, 0, 1),
    ],
)
def test_worker_count_never_returns_less_than_one(params, default, expected):
    assert worker_count(params, default) == expected
