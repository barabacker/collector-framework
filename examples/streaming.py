"""Streaming: take items as they arrive and stop when you have enough.

``collect()`` runs the whole crawl and hands back a list. ``stream()`` is the
other direction: the consumer's loop drives, items arrive over a channel bounded
by ``concurrency`` — so a slow consumer applies backpressure instead of letting
the crawl pile up in memory — and breaking out of the loop stops the crawl.

``open_crawl()`` is what makes the ``break`` safe: the HTTP session has to
outlive the iteration, and a consumer that walks away has to have the workers
stopped for it. Without it, ``break`` does not finalise the generator there and
then, and the crawl keeps going until Python gets round to it.

    uv run python examples/streaming.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from collector import Parser, Response, Settings, open_crawl


class Quotes(Parser):
    name = 'quotes-stream'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.3)

    async def parse(self, response: Response) -> Any:
        page = response.selector()
        for quote in page.css('div.quote'):
            yield {'author': quote.css('small.author::text').get()}

        next_page = page.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


async def main() -> None:
    # Scraped text is not ASCII and a Windows console is not UTF-8 by default,
    # which is a UnicodeEncodeError the first time an author is called André.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    wanted = 12

    async with open_crawl(Quotes) as crawl:
        seen: list[Any] = []
        async for quote in crawl.stream():
            seen.append(quote)
            print(f'{len(seen):>3}. {quote["author"]}')
            if len(seen) == wanted:
                break  # the crawl stops with it

    # 'cancelled', not 'done': the queue never drained, and a caller deciding
    # whether it has the whole site needs to be able to tell those apart.
    #
    # stats.items reads a little above `wanted`: the page being parsed when the
    # break happened had already emitted the rest of its quotes into the
    # channel. Backpressure bounds that overshoot to a page, not to nothing.
    print(f'\n{crawl.stats}')
    print(f'asked for {wanted}, crawled {crawl.stats.requests} pages')


if __name__ == '__main__':
    asyncio.run(main())
