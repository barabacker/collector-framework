"""The framework over real sockets, against https://mockhttp.org.

Every other test in this suite swaps ``HttpClient`` for ``FakeHttp``, so
``curl_cffi`` itself, the retry loop's sleeps, the ``Throttle`` lock and a
session outliving a cancelled ``stream()`` were never exercised. These drive the
public API (``run_parser`` / ``collect`` / ``open_crawler``) against a real HTTP
service and check what actually went out.

Marked ``network`` and excluded from the default run: ``uv run pytest -q`` still
needs no internet, because a suite that depends on a third party's uptime and
rate limit fails for reasons that have nothing to do with this repository. Run
them deliberately::

    uv run pytest -m network

**What the service cannot do.** mockhttp.org is an httpbin clone
(``jaredwray/mockhttp``), and its routes are stateless: ``/status/:code``
returns that code every time, with no ``Retry-After``, and ``/response-headers``
echoes any header asked for but only ever with a 200. So a *sequence* — "two
503s and then a 200" — has no endpoint here, and neither does a 429 that
carries a ``Retry-After``. Two consequences, both deliberate:

- the ``Retry-After`` tests below take the header from the real server and
  rewrite only the status code, in a response hook, to something retryable.
  The header, the socket, the parsing and the sleep are real; the status is not,
  and each such test says so;
- "a transport error and a retryable status under one attempt budget" cannot be
  staged at all. Each half is covered on its own, and ``test_client.py`` covers
  the interleaving against a fake session.

**How a retry is seen.** The service will not tell us how many times it was hit,
so ``RoundTrips`` — an ordinary request hook — counts what leaves this process.
That is also the level ``stats.requests`` is *not* counted at, which is what
makes the two comparable.
"""

from __future__ import annotations

import asyncio
import email.utils
import time
from typing import Any
from urllib.parse import quote

import pytest
from curl_cffi.requests.exceptions import RequestException

from collector import (
    BaseParser,
    Crawler,
    ParserContext,
    Response,
    RetryPolicy,
    Settings,
    collect,
    open_crawler,
    run_parser,
)
from collector.http import build_http_client

pytestmark = pytest.mark.network

BASE = 'https://mockhttp.org'


class RoundTrips:
    """A request hook that timestamps every round trip that actually goes out.

    Installed after the ``Throttle`` the factory adds, so its timestamps are
    hook *releases*: the pacing as the site would see it, without this machine's
    latency to the CDN in the way.
    """

    def __init__(self) -> None:
        self.at: list[float] = []

    async def __call__(self, method: str, url: str, kwargs: dict[str, Any]) -> None:
        self.at.append(time.monotonic())

    def __len__(self) -> int:
        return len(self.at)

    def gaps(self) -> list[float]:
        return [later - earlier for earlier, later in zip(self.at, self.at[1:], strict=False)]


def fast_retry(attempts: int = 3, **kwargs: Any) -> RetryPolicy:
    """A policy whose backoff is milliseconds — a test should not sit out a real one."""
    defaults = {'multiplier': 0.01, 'min_wait': 0.01, 'max_wait': 0.05}
    return RetryPolicy(attempts=attempts, **{**defaults, **kwargs})


def settings(**kwargs: Any) -> Settings:
    """Settings for the public service.

    ``impersonate`` is left at its default: against a real CDN the Chrome
    fingerprint is the point, and this is the only place it can be checked end
    to end. ``delay`` is a courtesy — the service is someone's gift and it rate
    limits — and it also means no test here fires a burst.
    """
    kwargs.setdefault('timeout', 30.0)
    kwargs.setdefault('delay', 0.1)
    kwargs.setdefault('retry', fast_retry())
    return Settings(**kwargs)


class Recording(BaseParser):
    """Keeps what it emitted, for a test that wants the items and the stats both.

    The sanctioned route for a synchronous caller: put the state on the parser,
    read it back off ``crawler.parser`` once the run is over. The crawler hands
    items to nobody but ``stream()``.
    """

    def __init__(self, ctx: ParserContext) -> None:
        super().__init__(ctx)
        self.items: list[Any] = []

    async def process_item(self, item: Any) -> None:
        self.items.append(item)


class Linked(Recording):
    """Walks ``/links/:n/:offset``: a page of links to pages that do the same.

    Every one of those pages links back, so following them without a ceiling
    never terminates — the framework de-duplicates nothing, by design.
    """

    name = 'links'

    async def parse(self, response: Response) -> Any:
        yield {'url': response.request.url}
        for href in response.selector().css('a::attr(href)').getall():
            yield response.follow(href)


# ── the transport is really there ───────────────────────────────────────────


def test_a_crawl_talks_to_the_real_service() -> None:
    """``/get`` echoes the request it received, headers included.

    Which is where the ``impersonate`` fingerprint can be checked end to end:
    a real TLS handshake, with a CDN on the other side of it.
    """

    class Get(BaseParser):
        name = 'mockhttp-get'
        start_urls = [f'{BASE}/get?page=2']
        settings = settings()

        async def parse(self, response: Response) -> Any:
            body = response.json()
            yield {
                'status': response.status,
                'method': body['method'],
                'params': body['queryParams'],
                'user_agent': body['headers'].get('user-agent', ''),
            }

    item = collect(Get)[0]
    assert item['status'] == 200
    assert item['method'] == 'GET'
    assert item['params'] == {'page': '2'}
    # A browser header set arrived, not a bare curl request.
    assert 'Chrome/' in item['user_agent']


def test_a_request_carries_its_own_transport_fields_over_the_wire() -> None:
    """``Request.http_kwargs()`` against real curl, not against a fake's ``**kwargs``."""

    class Poster(BaseParser):
        name = 'mockhttp-anything'
        settings = settings()

        async def start_requests(self) -> Any:
            yield self.request(
                f'{BASE}/anything',
                method='POST',
                headers={'X-Token': 'shibboleth'},
                params={'page': '2'},
                json={'field': 'value'},
            )

        async def parse(self, response: Response) -> Any:
            yield response.json()

    echoed = collect(Poster)[0]
    assert echoed['args'] == {'page': '2'}
    assert echoed['json'] == {'field': 'value'}
    assert echoed['headers']['x-token'] == 'shibboleth'


def test_redirects_are_followed_below_the_framework() -> None:
    """``/relative-redirect/:n`` chains n 302s down to ``/get``; curl follows them."""
    trips = RoundTrips()

    class Redirected(Recording):
        name = 'mockhttp-redirect'
        start_urls = [f'{BASE}/relative-redirect/2']
        settings = settings(request_hooks=(trips,))

        async def parse(self, response: Response) -> Any:
            yield {'status': response.status, 'final': str(response.raw.url)}

    crawler = run_parser(Redirected)
    item = crawler.parser.items[0]

    assert item['status'] == 200
    assert item['final'].endswith('/get')
    # Three hops on the wire, one request through this framework: curl's business.
    assert crawler.stats.requests == 1
    assert len(trips) == 1


# ── retries ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('status', [429, 503])
def test_a_retryable_status_spends_the_whole_attempt_budget(status: int) -> None:
    """``/status/:code`` is stateless, so every attempt fails and the parser sees it.

    A retryable status is not an error: once the budget is spent the response is
    handed over, and the crawl finishes clean.
    """
    trips = RoundTrips()

    class Failing(Recording):
        name = f'mockhttp-{status}'
        start_urls = [f'{BASE}/status/{status}']
        settings = settings(retry=fast_retry(attempts=3), request_hooks=(trips,))

        async def parse(self, response: Response) -> Any:
            yield {'status': response.status}

    crawler = run_parser(Failing)

    assert crawler.parser.items == [{'status': status}]
    assert len(trips) == 3  # three round trips …
    assert crawler.stats.requests == 1  # … for one request the parser asked for
    assert crawler.errors == []


def test_a_transport_error_spends_the_same_budget_and_then_fails_the_crawl() -> None:
    """``/delay/:n`` held past our timeout is the other failure mode the budget covers."""
    trips = RoundTrips()

    class Slow(BaseParser):
        name = 'mockhttp-timeout'
        start_urls = [f'{BASE}/delay/5']
        settings = settings(timeout=1.0, retry=fast_retry(attempts=2), request_hooks=(trips,))

        async def parse(self, response: Response) -> Any:
            yield {'status': response.status}

    with pytest.raises(RequestException) as raised:
        run_parser(Slow)

    assert len(trips) == 2
    # The crawler rides out on the exception, with the failure still attached.
    crawler = raised.value.crawler
    assert crawler.stats.errors == 1
    assert [request.url for request, _ in crawler.errors] == list(Slow.start_urls)


def test_stats_requests_counts_queued_requests_not_round_trips() -> None:
    """The ``Stats`` contract, checked where it can actually be contradicted."""
    trips = RoundTrips()

    class TwoFailing(Recording):
        name = 'mockhttp-counted'
        start_urls = [f'{BASE}/status/503', f'{BASE}/status/502']
        settings = settings(retry=fast_retry(attempts=2), request_hooks=(trips,))

        async def parse(self, response: Response) -> Any:
            yield {'status': response.status}

    crawler = run_parser(TwoFailing)

    assert sorted(item['status'] for item in crawler.parser.items) == [502, 503]
    assert len(trips) == 4
    assert crawler.stats.requests == 2
    assert crawler.stats.items == 2
    assert crawler.stats.reason == 'done'


def test_a_hook_driven_retry_is_paced_but_spends_no_attempt() -> None:
    """The escape hatch for an anti-bot challenge, over a real socket.

    ``retry()`` is not a failed attempt — the budget of one is never touched —
    but it is a second request to the site, so the ``Throttle`` has to hold its
    gap in front of it.
    """
    trips = RoundTrips()
    delay = 0.75

    async def solve(response: Any, *, session: Any, retry: Any) -> Any:
        return await retry() if len(trips) == 1 else response

    class Challenged(Recording):
        name = 'mockhttp-hook-retry'
        start_urls = [f'{BASE}/get']
        settings = settings(
            delay=delay,
            retry=fast_retry(attempts=1),  # one attempt: the retry cannot be the policy's
            request_hooks=(trips,),
            response_hooks=(solve,),
        )

        async def parse(self, response: Response) -> Any:
            yield {'status': response.status}

    crawler = run_parser(Challenged)

    assert crawler.parser.items == [{'status': 200}]
    assert len(trips) == 2
    assert trips.gaps()[0] >= delay * 0.9
    assert crawler.stats.requests == 1


# ── Retry-After: real header, real sleep, borrowed status ───────────────────
#
# No route pairs a retryable status with a Retry-After, so these take the header
# from /response-headers — where the server really does send it — and rewrite
# only ``status_code`` in a response hook. What is under test is unchanged: the
# header as it came off the wire, the parsing, and the wait before the next real
# round trip.


async def as_429(response: Any, *, session: Any, retry: Any) -> Any:
    """Response hook: call the server's 200 a 429, and change nothing else."""
    response.status_code = 429
    return response


def test_retry_after_in_seconds_wins_over_the_backoff() -> None:
    """The policy would wait 5s; the server's header asks for 1."""
    trips = RoundTrips()

    class Limited(BaseParser):
        name = 'mockhttp-ra-seconds'
        start_urls = [f'{BASE}/response-headers?Retry-After=1']
        settings = settings(
            retry=RetryPolicy(attempts=2, multiplier=5.0, min_wait=5.0, max_wait=5.0),
            request_hooks=(trips,),
            response_hooks=(as_429,),
        )

        async def parse(self, response: Response) -> Any:
            yield {'retry_after': response.raw.headers.get('Retry-After')}

    assert collect(Limited) == [{'retry_after': '1'}]
    assert len(trips) == 2
    assert 0.9 <= trips.gaps()[0] < 4.0


def test_retry_after_as_an_http_date_wins_over_the_backoff() -> None:
    """Same header, date form: the other branch of ``_retry_after_seconds``."""
    trips = RoundTrips()
    when = quote(email.utils.formatdate(time.time() + 2, usegmt=True))

    class Limited(BaseParser):
        name = 'mockhttp-ra-date'
        start_urls = [f'{BASE}/response-headers?Retry-After={when}']
        settings = settings(
            retry=RetryPolicy(attempts=2, multiplier=8.0, min_wait=8.0, max_wait=8.0),
            request_hooks=(trips,),
            response_hooks=(as_429,),
        )

        async def parse(self, response: Response) -> Any:
            yield {'retry_after': response.raw.headers.get('Retry-After')}

    item = collect(Limited)[0]
    assert item['retry_after'].endswith('GMT')
    assert len(trips) == 2
    # An HTTP-date is whole seconds, and the round trip eats into the gap.
    assert trips.gaps()[0] < 6.0


def test_a_retry_after_longer_than_we_will_wait_ends_the_retrying() -> None:
    trips = RoundTrips()

    class Limited(BaseParser):
        name = 'mockhttp-ra-too-long'
        start_urls = [f'{BASE}/response-headers?Retry-After=120']
        settings = settings(
            retry=fast_retry(attempts=4, max_retry_after=1.0),
            request_hooks=(trips,),
            response_hooks=(as_429,),
        )

        async def parse(self, response: Response) -> Any:
            yield {'status': response.status}

    started = time.monotonic()
    assert collect(Limited) == [{'status': 429}]

    # Budget was four, but the server asked for two minutes: give up, don't sleep.
    assert len(trips) == 1
    assert time.monotonic() - started < 30.0


# ── pacing, concurrency and the safety valve ────────────────────────────────


def test_throttle_paces_the_whole_crawl_not_each_worker() -> None:
    """``delay`` is a ceiling of ``1/delay`` requests per second for the crawl.

    The lock is held across the sleep, so four workers do not each get their own
    interval. Four unlocked workers would fire the whole fan at once, and the
    gaps below would be zero.
    """
    trips = RoundTrips()
    delay = 0.4

    class Fan(BaseParser):
        name = 'mockhttp-fan'
        start_urls = [f'{BASE}/links/5/0']
        settings = settings(concurrency=4, delay=delay, request_hooks=(trips,))

        async def parse(self, response: Response) -> Any:
            for href in response.selector().css('a::attr(href)').getall():
                yield response.follow(href, callback=self.parse_leaf)

        async def parse_leaf(self, response: Response) -> Any:
            yield {'url': response.request.url}

    crawler = run_parser(Fan)

    assert crawler.stats.requests == 5  # the index page plus four links
    assert len(trips) == 5
    assert min(trips.gaps()) >= delay * 0.9
    # Four workers bought nothing: the crawl still takes four gaps.
    assert crawler.stats.elapsed >= 4 * delay


def test_max_requests_stops_a_crawl_that_would_never_end() -> None:
    """Those pages link back to each other, and nothing here de-duplicates a URL."""
    trips = RoundTrips()

    class Endless(Linked):
        start_urls = [f'{BASE}/links/5/0']
        settings = settings(max_requests=6, request_hooks=(trips,))

    crawler = run_parser(Endless)

    assert crawler.stats.requests == 6
    assert crawler.stats.reason == 'max_requests'
    # The valve closes before the socket, not after.
    assert len(trips) == 6


# ── stream() and walking away from it ───────────────────────────────────────


async def test_break_under_open_crawler_stops_the_crawl_and_the_session() -> None:
    trips = RoundTrips()

    class Endless(Linked):
        start_urls = [f'{BASE}/links/8/0']
        settings = settings(delay=0.4, request_hooks=(trips,))

    async with open_crawler(Endless) as crawler:
        async for _item in crawler.stream():
            break

    settled = len(trips)
    await asyncio.sleep(1.5)
    assert len(trips) == settled  # nothing else ever left this process


async def test_break_without_closing_the_generator_leaves_the_crawl_running() -> None:
    """The documented trap, on real sockets: ``break`` does not finalise a generator.

    Held by a name here rather than left to the garbage collector, so the test
    measures the framework and not CPython's finalisation timing. Until
    ``aclose()`` — or ``open_crawler()``, or ``contextlib.aclosing()`` — the
    workers keep fetching and the HTTP session has to stay open under them.
    """
    trips = RoundTrips()

    class Endless(Linked):
        start_urls = [f'{BASE}/links/8/0']
        settings = settings(delay=0.3, request_hooks=(trips,))

    http = build_http_client(Endless)
    async with http:
        crawler = Crawler(Endless(ParserContext(http=http)))
        stream = crawler.stream()
        async for _item in stream:
            break

        after_break = len(trips)
        await asyncio.sleep(1.5)
        assert len(trips) > after_break  # still crawling, session still alive

        await crawler.aclose()
        settled = len(trips)
        await asyncio.sleep(1.0)
        assert len(trips) == settled

        await stream.aclose()
