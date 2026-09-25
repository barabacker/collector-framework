"""Throttle spaces requests out, including across concurrent workers."""

from __future__ import annotations

import asyncio

import pytest
from tests.conftest import FakeResponse

from collector.http import AutoThrottle, Throttle


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


# ── AutoThrottle ─────────────────────────────────────────────────────────────


async def test_autothrottle_widens_the_delay_on_a_retryable_status():
    throttle = Throttle(delay=1.0)
    auto = AutoThrottle(throttle, ceiling=10.0)

    response = await auto(FakeResponse(status_code=429), session=None, retry=None)

    assert throttle.delay == 2.0
    assert response.status_code == 429  # returned unchanged, like any response hook


async def test_widening_is_capped_at_the_ceiling():
    throttle = Throttle(delay=6.0)
    auto = AutoThrottle(throttle, ceiling=10.0, factor=3.0)

    await auto(FakeResponse(status_code=503), session=None, retry=None)

    assert throttle.delay == 10.0  # 6.0 * 3.0 would be 18.0


async def test_autothrottle_narrows_the_delay_on_a_normal_response():
    throttle = Throttle(delay=1.0)
    auto = AutoThrottle(throttle, ceiling=10.0)
    throttle.delay = 4.0  # simulate an earlier widening

    await auto(FakeResponse(status_code=200), session=None, retry=None)

    assert throttle.delay == 2.0


async def test_narrowing_never_drops_below_the_floor():
    """The floor is the delay Settings declared — AutoThrottle never undercuts it."""
    throttle = Throttle(delay=1.0)
    auto = AutoThrottle(throttle, ceiling=10.0)

    for _ in range(5):
        await auto(FakeResponse(status_code=200), session=None, retry=None)

    assert throttle.delay == 1.0


async def test_a_custom_status_set_is_honoured():
    throttle = Throttle(delay=1.0)
    auto = AutoThrottle(throttle, ceiling=10.0, statuses={403})

    await auto(FakeResponse(status_code=429), session=None, retry=None)
    assert throttle.delay == 1.0  # 429 isn't in the custom set: no widening

    await auto(FakeResponse(status_code=403), session=None, retry=None)
    assert throttle.delay == 2.0
