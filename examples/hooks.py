"""Hooks: pace and log every round trip, and solve a challenge without the parser knowing.

Two ordered tuples on the client. Request hooks run before each round trip and
may mutate what is about to be sent; response hooks run after it and may return
the response, return a different one, or ``await retry()`` to make the request
again.

That ``retry()`` is the interesting half. It is how an anti-bot challenge, a
login or an expired token gets dealt with in one place instead of in every
parser: the hook fixes the session and asks for the request again. It spends no
part of the ``RetryPolicy`` budget — solving a challenge is not a failed attempt
— but it *is* another request to the site, so it queues behind ``Throttle`` like
any other.

``/bearer`` on mockhttp.org answers 401 without an ``Authorization`` header and
200 with one, which is a challenge small enough to read.

    uv run python examples/hooks.py
"""

from __future__ import annotations

import time
from typing import Any

from collector import Parser, Response, Settings, collect

BASE = 'https://mockhttp.org'
TOKEN = 'a-token-fetched-from-somewhere'


class Stopwatch:
    """Request hook: record when each round trip was actually let go.

    A hook is any callable of the right shape, so one with state is just a class
    with ``__call__``. This one shows the pacing ``delay`` buys — and that a
    retry, of either kind, is a round trip and gets paced too.
    """

    def __init__(self) -> None:
        self.at: list[tuple[str, float]] = []

    async def __call__(self, method: str, url: str, kwargs: dict[str, Any]) -> None:
        self.at.append((url, time.monotonic()))

    def gaps(self) -> list[float]:
        stamps = [when for _, when in self.at]
        return [round(b - a, 2) for a, b in zip(stamps, stamps[1:], strict=False)]


async def send_referer(method: str, url: str, kwargs: dict[str, Any]) -> None:
    """Request hook: add a header to every request, without touching any parser."""
    headers = kwargs.setdefault('headers', {})
    headers.setdefault('Referer', BASE)


async def authenticate(response: Any, *, session: Any, retry: Any) -> Any:
    """Response hook: on a 401, put the token on the session and ask again.

    The session is handed in precisely for this: a header or a cookie set here
    holds for every later request too, so the challenge is solved once rather
    than once per page.
    """
    if response.status_code != 401:
        return response

    session.headers['Authorization'] = f'Bearer {TOKEN}'
    return await retry()


stopwatch = Stopwatch()


class Protected(Parser):
    name = 'protected'
    start_urls = [f'{BASE}/bearer', f'{BASE}/bearer']
    settings = Settings(
        delay=0.5,
        # Order is the whole contract: these run front to back before each round
        # trip, after the logging hook and the Throttle the factory installs.
        request_hooks=(stopwatch, send_referer),
        response_hooks=(authenticate,),
    )

    async def parse(self, response: Response) -> Any:
        yield {'status': response.status, 'body': response.json()}


def main() -> None:
    items = collect(Protected)
    for item in items:
        print(item)

    # Two requests, three round trips: the first one was answered 401, the hook
    # authenticated and asked again, and the second request already had the
    # header. Every gap is at least the declared delay, the hook's retry too.
    print(f'\nround trips: {len(stopwatch.at)} for {len(items)} requests')
    print(f'gaps: {stopwatch.gaps()} (delay was {Protected.settings.delay})')


if __name__ == '__main__':
    main()
