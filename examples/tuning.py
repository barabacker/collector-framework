"""How hard to lean on a site, and how a job overrides it without touching the crawler.

``Settings`` is one frozen dataclass per crawler holding everything about how it
talks to a site, so a caller never carries the site's quirks. A subclass narrows
its parent's with ``dataclasses.replace`` — the fields it does not name keep the
parent's values.

Two of those knobs can also be overridden per run, through ``params``: strings
from a CLI flag or a job payload, where a bad value falls back to what the
crawler declared rather than killing the crawl.

A crawler's own knobs are declared the same way ``settings`` are — a frozen
dataclass instance, ``params`` — and a run's values reach it typed on
``self.params``. Those are checked before the first request: a bad value or a
misspelt name fails the run instead of quietly crawling something else.

Worth knowing about pacing: ``delay`` is a ceiling of ``1/delay`` requests per
second *for the whole crawl*, not per worker — the ``Throttle`` holds its gap
behind a lock. Concurrency above that ceiling buys nothing, which the two runs
below show by finishing in the same time.

    uv run python examples/tuning.py
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from collector import Crawler, Response, RetryPolicy, Settings, run_crawler

BASE = 'https://mockhttp.org'


class Fan(Crawler):
    """One index page linking several others: work a crawl can spread out."""

    name = 'fan'
    start_urls = [f'{BASE}/links/6/0']
    settings = Settings(
        # ── transport ──
        impersonate='chrome',  # the default; a curl fingerprint is 'chrome' → None
        timeout=30.0,
        # ── pacing ──
        concurrency=4,
        delay=0.25,
        delay_jitter=0.1,  # so the crawl does not hit the site on a metronome
        # ── safety valve ──
        max_requests=7,  # those pages link back to each other; nothing de-duplicates
        # ── failure ──
        retry=RetryPolicy(attempts=3, multiplier=0.5, max_retry_after=30.0),
    )

    async def parse(self, response: Response) -> Any:
        yield {'url': response.request.url}
        for href in response.selector().css('a::attr(href)').getall():
            yield response.follow(href)


class Polite(Fan):
    """A subclass narrows its parent's settings; the rest is inherited."""

    name = 'fan-polite'
    settings = replace(Fan.settings, concurrency=1, delay=0.25)


@dataclass(frozen=True)
class FanParams:
    #: How many of the linked pages to follow.
    fan_out: int = 6


class Shallow(Fan):
    """A crawler's own per-run knob: declared, typed, checked up front."""

    name = 'fan-shallow'
    params = FanParams()

    async def parse(self, response: Response) -> Any:
        yield {'url': response.request.url}
        if response.request.url != self.start_urls[0]:
            return
        links = response.selector().css('a::attr(href)').getall()
        for href in links[: self.params.fan_out]:
            yield response.follow(href)


def report(label: str, crawler_cls: type[Crawler], **kwargs: Any) -> None:
    crawl = run_crawler(crawler_cls, **kwargs)
    stats = crawl.stats
    print(
        f'{label:<28} {stats.requests} requests, {stats.items} items, '
        f'{stats.elapsed:.1f}s, reason={stats.reason!r}'
    )


def main() -> None:
    report('concurrency=4, delay=0.25', Fan)
    # Same wall clock: the delay was the ceiling all along, not the worker count.
    report('concurrency=1, delay=0.25', Polite)
    # params win over what the crawler declared — and a junk value falls back to
    # it instead of raising.
    report("params concurrency='2'", Fan, params={'concurrency': '2', 'max_requests': '3'})
    report("params concurrency='oops'", Fan, params={'concurrency': 'oops', 'max_requests': '3'})
    # A crawler's own param, from a string as a CLI would pass it — and a bad
    # one fails before a single request instead of falling back.
    report("params fan_out='2'", Shallow, params={'fan_out': '2'})
    label = "params fan_out='two'"
    try:
        run_crawler(Shallow, params={'fan_out': 'two'})
    except ValueError as exc:
        print(f'{label:<28} refused: {exc}')


if __name__ == '__main__':
    main()
