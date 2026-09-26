"""Many crawlers at once: a cap, each crawler's items handled on their own, and
an outcome per crawler as it finishes.

``crawl_many()`` opens each crawler as ``open_crawl()`` would and hands it to
``consume``, which decides what to do with its items — here, count them. A
crawler that fails lands in its own outcome, stats and all, and the others
carry on.

    uv run python examples/many.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from collector import Crawl, Crawler, Response, Settings, crawl_many


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.3, max_requests=2)

    async def parse(self, response: Response) -> Any:
        page = response.selector()
        for quote in page.css('div.quote'):
            yield {'author': quote.css('small.author::text').get()}
        next_page = page.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


class Love(Quotes):
    """Same shape, another start: a second site as far as crawl_many knows."""

    name = 'love'
    start_urls = ['https://quotes.toscrape.com/tag/love/']


async def run() -> None:
    counts: dict[str, int] = {}

    async def consume(crawl: Crawl) -> None:
        name = crawl.crawler.name
        async for _item in crawl.stream():
            counts[name] = counts.get(name, 0) + 1

    async for outcome in crawl_many([Quotes, Love], concurrency=2, consume=consume):
        name = outcome.crawler_cls.name
        status = f'failed: {outcome.error!r}' if outcome.error else f'{counts.get(name, 0)} items'
        print(f'{name:<8} {status} in {outcome.elapsed:.1f}s')
    print(f'total: {sum(counts.values())} items')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(run())


if __name__ == '__main__':
    main()
