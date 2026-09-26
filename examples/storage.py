"""Keeping what a crawl emits: a ``SqliteDataset``, read back and exported.

Pass a dataset to a run and every item the crawl emits is written to it, under
the crawler's name — the crawler itself does not change. ``SqliteDataset``
keeps them in a file you can open in the ``sqlite3`` shell while the crawl is
still going; ``export_to()`` writes them out as JSON, JSON Lines or CSV.

    uv run python examples/storage.py
    sqlite3 quotes.db "SELECT json_extract(item, '$.author'), count(*) FROM items GROUP BY 1"
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import aclosing
from pathlib import Path
from typing import Any

from collector import Crawler, Response, Settings, SqliteDataset, crawl


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.3, max_requests=2)

    async def parse(self, response: Response) -> Any:
        page = response.selector()
        for quote in page.css('div.quote'):
            yield {
                'author': quote.css('small.author::text').get(),
                'text': quote.css('span.text::text').get(),
                'tags': quote.css('div.tags a.tag::text').getall(),
            }
        next_page = page.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


async def run() -> None:
    # A dataset file is appended to across runs; start this demo from scratch.
    for leftover in ('quotes.db', 'quotes.db-wal', 'quotes.db-shm'):
        Path(leftover).unlink(missing_ok=True)

    async with SqliteDataset('quotes.db') as dataset:
        finished = await crawl(Quotes, dataset=dataset)
        exported = await dataset.export_to('quotes.csv')
        async with aclosing(dataset.iterate_items()) as items:
            first = await anext(items, None)

    print(f'{finished.stats.items} items stored in quotes.db, {exported} exported to quotes.csv')
    if first:
        print(f'  first: {first["author"]} — {first["text"][:50]}…')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(run())


if __name__ == '__main__':
    main()
