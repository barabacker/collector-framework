"""HttpClient: hooks wrap every round trip, and network errors are retried."""

from __future__ import annotations

import email.utils
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from curl_cffi.requests.exceptions import RequestException
from tests.conftest import FakeResponse

from collector import RetryPolicy
from collector.http import HttpClient


@pytest.fixture
def no_sleep(monkeypatch):
    """Make backoff instant and record how long each wait would have been."""
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr('collector.http.client.asyncio.sleep', _sleep)
    return waits


class _FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True


async def test_request_hook_can_mutate_kwargs():
    session = _FakeSession([FakeResponse(text='ok')])

    async def add_header(method: str, url: str, kwargs: dict[str, Any]) -> None:
        kwargs.setdefault('headers', {})['X-Test'] = '1'

    client = HttpClient(session, request_hooks=(add_header,))

    await client.request('GET', 'https://example.test/')

    assert session.calls[0][2]['headers'] == {'X-Test': '1'}


async def test_response_hook_may_replace_the_response():
    session = _FakeSession([FakeResponse(text='original')])

    async def swap(response: Any, *, session: Any, retry: Any) -> Any:
        return FakeResponse(text='replaced')

    client = HttpClient(session, response_hooks=(swap,))

    assert (await client.request('GET', 'https://example.test/')).text == 'replaced'


async def test_response_hook_can_retry_the_request():
    """A hook that solves a challenge re-runs the request and returns the new one."""
    session = _FakeSession([FakeResponse(text='challenge'), FakeResponse(text='content')])

    async def solve(response: Any, *, session: Any, retry: Any) -> Any:
        if response.text == 'challenge':
            return await retry()
        return response

    client = HttpClient(session, response_hooks=(solve,))

    assert (await client.request('GET', 'https://example.test/')).text == 'content'
    assert len(session.calls) == 2


async def test_a_hook_driven_retry_runs_the_request_hooks_again():
    """It is another request to the site, so Throttle must get to pace it.

    The hook-driven ``retry()`` is not a failed attempt and spends no attempt
    budget — but it does put a request on the wire, and a request that skips the
    request hooks skips the pacing the parser declared with ``delay``.
    """
    session = _FakeSession([FakeResponse(text='challenge'), FakeResponse(text='content')])
    paced: list[str] = []

    async def pace(method: str, url: str, kwargs: dict[str, Any]) -> None:
        paced.append(url)

    async def solve(response: Any, *, session: Any, retry: Any) -> Any:
        return await retry() if response.text == 'challenge' else response

    client = HttpClient(session, request_hooks=(pace,), response_hooks=(solve,))

    assert (await client.request('GET', 'https://example.test/')).text == 'content'
    assert paced == ['https://example.test/'] * 2


async def test_network_errors_are_retried_then_the_response_returned(no_sleep):
    session = _FakeSession([RequestException('boom'), FakeResponse(text='ok')])
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).text == 'ok'
    assert len(session.calls) == 2


async def test_retries_give_up_and_reraise(no_sleep):
    session = _FakeSession([RequestException('boom')] * 4)
    client = HttpClient(session)

    with pytest.raises(RequestException):
        await client.request('GET', 'https://example.test/')
    assert len(session.calls) == 4


async def test_backoff_grows_and_is_clamped(no_sleep):
    session = _FakeSession([RequestException('boom')] * 5)
    policy = RetryPolicy(attempts=5, min_wait=0.5, max_wait=4.0)
    client = HttpClient(session, retry=policy)

    with pytest.raises(RequestException):
        await client.request('GET', 'https://example.test/')
    assert no_sleep == [1.0, 2.0, 4.0, 4.0]


async def test_retryable_status_is_retried(no_sleep):
    session = _FakeSession([FakeResponse(status_code=503), FakeResponse(text='ok')])
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).text == 'ok'
    assert len(session.calls) == 2


async def test_non_retryable_status_is_returned_as_is(no_sleep):
    session = _FakeSession([FakeResponse(status_code=404, text='gone')])
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).status_code == 404
    assert len(session.calls) == 1
    assert no_sleep == []


async def test_last_attempt_returns_the_bad_response_rather_than_raising(no_sleep):
    session = _FakeSession([FakeResponse(status_code=503)] * 4)
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).status_code == 503
    assert len(session.calls) == 4


async def test_retry_after_header_wins_over_backoff(no_sleep):
    session = _FakeSession(
        [FakeResponse(status_code=429, headers={'Retry-After': '7'}), FakeResponse(text='ok')]
    )
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).text == 'ok'
    assert no_sleep == [7.0]


async def test_retry_after_longer_than_the_cap_stops_the_retries(no_sleep):
    """Waiting out a ten-minute cooldown is the caller's decision, not ours."""
    session = _FakeSession(
        [FakeResponse(status_code=429, headers={'Retry-After': '600'}), FakeResponse(text='ok')]
    )
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).status_code == 429
    assert len(session.calls) == 1
    assert no_sleep == []


async def test_retry_after_may_be_an_http_date(no_sleep):
    session = _FakeSession(
        [
            FakeResponse(
                status_code=503,
                headers={
                    'Retry-After': email.utils.format_datetime(
                        datetime.now(UTC) + timedelta(seconds=30)
                    )
                },
            ),
            FakeResponse(text='ok'),
        ]
    )
    client = HttpClient(session)

    assert (await client.request('GET', 'https://example.test/')).text == 'ok'
    assert 25 <= no_sleep[0] <= 31


async def test_request_hooks_rerun_from_the_original_kwargs_on_retry(no_sleep):
    """A hook that appends must not stack across attempts."""
    session = _FakeSession([FakeResponse(status_code=503), FakeResponse(text='ok')])

    async def add_marker(method: str, url: str, kwargs: dict[str, Any]) -> None:
        kwargs['headers'] = {**kwargs.get('headers', {}), 'X-Try': 'yes'}

    client = HttpClient(session, request_hooks=(add_marker,))

    await client.request('GET', 'https://example.test/', headers={'X-Base': '1'})

    assert session.calls[1][2]['headers'] == {'X-Base': '1', 'X-Try': 'yes'}


async def test_context_manager_closes_the_session():
    session = _FakeSession([])
    async with HttpClient(session):
        pass
    assert session.closed
