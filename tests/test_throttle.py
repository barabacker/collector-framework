"""Throttle spaces requests out, including across concurrent workers."""

from __future__ import annotations

import asyncio

import pytest

from collector import Throttle


@pytest.fixture
def clock(monkeypatch):
    """A fake monotonic clock that sleeping advances, so no test really waits."""
    now = {'t': 0.0}

    async def _sleep(seconds: float) -> None:
        now['t'] += seconds

    monkeypatch.setattr('collector.http.hooks.time.monotonic', lambda: now['t'])
    monkeypatch.setattr('collector.http.hooks.asyncio.sleep', _sleep)
    return now


async def test_first_request_is_not_delayed(clock):
    throttle = Throttle(delay=2.0)
    await throttle('GET', 'https://example.test/', {})
    assert clock['t'] == 0.0


async def test_consecutive_requests_are_spaced_by_delay(clock):
    throttle = Throttle(delay=2.0)
    for _ in range(3):
        await throttle('GET', 'https://example.test/', {})
    assert clock['t'] == 4.0


async def test_concurrent_workers_still_queue_behind_the_gap(clock):
    """Without the lock, parallel workers would all fire at once."""
    throttle = Throttle(delay=1.0)
    await asyncio.gather(*(throttle('GET', 'https://example.test/', {}) for _ in range(4)))
    assert clock['t'] == 3.0


async def test_jitter_adds_a_bounded_random_extra(clock, monkeypatch):
    monkeypatch.setattr('collector.http.hooks.random.uniform', lambda a, b: b)
    throttle = Throttle(delay=1.0, jitter=0.5)
    for _ in range(2):
        await throttle('GET', 'https://example.test/', {})
    assert clock['t'] == 1.5
