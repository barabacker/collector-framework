"""When pages fail: one bad page does not end the crawl, and none of them are lost.

A request that blows up in ``parse()`` is collected, not fatal — the worker
takes the next one, and the crawl finishes. Only at the end does ``run()``
re-raise the first failure, so a bad page cannot pass silently either.

Which leaves the problem this example is really about: raising drops the crawl
with the frame that held it, so a crawl that survived twenty bad pages would
report one and lose nineteen. They ride out on a ``CrawlError`` instead, as
``exc.crawl`` — with the stats, and every failure paired with the request that
caused it. The original failure is still there too, as ``exc.__cause__``.

A retryable status is *not* a failure: 429 and the 5xx family are retried inside
the HTTP client, and once the attempt budget is spent the response is handed to
``parse()`` as it is. What to do about a 404 is the crawler's decision, which is
why this one makes it explicitly.

    uv run python examples/errors.py
"""

from __future__ import annotations

from typing import Any

from collector import Crawler, CrawlError, Response, RetryPolicy, Settings, run_crawler

BASE = 'https://mockhttp.org'


class Strict(Crawler):
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


def main() -> None:
    try:
        crawl = run_crawler(Strict)
    except CrawlError as exc:
        crawl = exc.crawl
        print(f'raised: {exc} (cause: {exc.__cause__!r})')

    print(crawl.stats)
    print(f'\n{len(crawl.errors)} of {crawl.stats.requests} requests failed:')
    for request, error in crawl.errors:
        print(f'  {request.method} {request.url} -> {error}')

    # The two good pages were still parsed and emitted; the crawl did not stop
    # at the first bad one.
    print(f'\nitems emitted anyway: {crawl.stats.items}')


if __name__ == '__main__':
    main()
