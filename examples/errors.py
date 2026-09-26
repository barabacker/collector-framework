"""When pages fail: how many a crawl tolerates, and what it keeps of them.

A request that blows up in ``parse()`` is collected, never lost: the worker
takes the next one, and every failure is kept with the request that caused it.
How many failures a crawl tolerates is ``Settings.max_errors``:

- ``0`` (the default) — the first failure stops the crawl and fails it, without
  sending the rest;
- ``N`` — up to N failures the crawl carries on and succeeds, the failures still
  in ``crawl.errors``; one more stops and fails it;
- ``None`` — failures never stop or fail it.

A crawl that fails still reports everything: it rides out on a ``CrawlError``
as ``exc.crawl`` — the stats, and every failure with its request — and the
original failure is ``exc.__cause__``.

A retryable status is *not* a failure: 429 and the 5xx family are retried inside
the HTTP client, and once the attempt budget is spent the response is handed to
``parse()`` as it is. What to do about a 404 is the crawler's decision, which is
why this one makes it explicitly.

    uv run python examples/errors.py
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from collector import Crawler, CrawlError, Response, RetryPolicy, Settings, run_crawler

BASE = 'https://mockhttp.org'


class Strict(Crawler):
    """The default: the first failure ends it."""

    name = 'strict'
    start_urls = [
        f'{BASE}/get?page=1',
        f'{BASE}/status/404',  # not retryable: the request itself is wrong
        f'{BASE}/status/503',  # retried, then handed over when the budget runs out
        f'{BASE}/get?page=2',
    ]
    settings = Settings(
        delay=0.2,
        # Small numbers so the example does not sit out a real backoff.
        retry=RetryPolicy(attempts=2, multiplier=0.2, min_wait=0.2, max_wait=0.5),
    )

    async def parse(self, response: Response) -> Any:
        if response.status != 200:
            raise ValueError(f'{response.status} for {response.request.url}')
        yield {'url': response.request.url, 'method': response.json()['method']}


class Fragile(Strict):
    """Tolerates one failure; the second stops it."""

    name = 'fragile'
    settings = replace(Strict.settings, max_errors=1)


class Tolerant(Strict):
    """Tolerates both bad pages, and succeeds."""

    name = 'tolerant'
    settings = replace(Strict.settings, max_errors=2)


def report(crawler_cls: type[Crawler]) -> None:
    try:
        crawl = run_crawler(crawler_cls)
        outcome = 'succeeded'
    except CrawlError as exc:
        crawl = exc.crawl
        outcome = f'failed: {exc.__cause__}'

    stats = crawl.stats
    print(f'{crawler_cls.name:<9} {outcome}')
    print(
        f'          {stats.requests} sent, {stats.errors} failed, {stats.items} items, '
        f'reason={stats.reason!r}'
    )
    for request, error in crawl.errors:
        print(f'          {request.url} -> {error}')


def main() -> None:
    report(Strict)
    report(Fragile)
    report(Tolerant)


if __name__ == '__main__':
    main()
