"""Settings and RetryPolicy: defaults, immutability, backoff arithmetic."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from collector import DEFAULT_RETRY_STATUSES, BaseParser, RetryPolicy, Settings


def test_defaults_are_conservative():
    s = Settings()
    assert (s.concurrency, s.delay, s.delay_jitter) == (1, 0.0, 0.0)
    assert s.proxy is None
    assert s.impersonate == 'chrome'
    assert s.retry.attempts == 4


def test_settings_are_frozen():
    with pytest.raises(FrozenInstanceError):
        Settings().concurrency = 8  # type: ignore[misc]


def test_replace_narrows_without_touching_the_original():
    base = Settings(timeout=10.0, concurrency=2)
    narrowed = replace(base, timeout=1.0)
    assert (narrowed.timeout, narrowed.concurrency) == (1.0, 2)
    assert base.timeout == 10.0


def test_base_parser_has_default_settings():
    assert BaseParser.settings == Settings()


def test_retry_statuses_are_the_transient_ones():
    assert {429, 500, 502, 503, 504} == DEFAULT_RETRY_STATUSES
    assert 404 not in DEFAULT_RETRY_STATUSES


@pytest.mark.parametrize(
    ('attempt', 'expected'),
    [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (10, 60.0)],
)
def test_backoff_doubles_up_to_max_wait(attempt, expected):
    assert RetryPolicy().backoff(attempt) == expected


def test_backoff_respects_min_wait():
    assert RetryPolicy(multiplier=0.01, min_wait=0.5).backoff(1) == 0.5
