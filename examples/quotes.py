"""Pagination: follow a chain of pages and emit an item per quote.

The shape most scrapes have. ``parse()`` yields items and then yields the next
request, so the crawl walks the chain one page at a time — page N + 1 is only
known once page N is parsed, which is why a chain cannot be parallelised and
``concurrency`` buys nothing here.

    uv run python examples/quotes.py
"""

from __future__ import annotations

import sys
from typing import Any

from collector import Crawler, Response, Settings, collect


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.5)

    async def parse(self, response: Response) -> Any:
        page = response.selector()

        for quote in page.css('div.quote'):
            yield {
                'text': quote.css('span.text::text').get(),
                'author': quote.css('small.author::text').get(),
                'tags': quote.css('div.tags a.tag::text').getall(),
            }

        # One selector for both queries: the page is parsed once either way,
        # but naming it says that out loud.
        next_page = page.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


def main() -> None:
    # Scraped text is not ASCII and a Windows console is not UTF-8 by default,
    # which is a UnicodeEncodeError the first time an author is called André.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    quotes = collect(Quotes)
    print(f'{len(quotes)} quotes')
    for quote in quotes[:3]:
        print(f'  {quote["author"]}: {quote["text"][:60]}…')


if __name__ == '__main__':
    main()
