"""Writing items somewhere: ``process_item()``, ``sink``, and the crawler afterwards.

The framework stores nothing and knows no item schema. An application overrides
``process_item()`` and writes to ``ctx.sink`` — whatever it passed in, handed
back untouched — which is how a database, a file or a queue gets involved
without this package knowing any of them exist.

Anything the run accumulates lives on the crawler, and ``crawl.crawler`` is the
instance that ran, so a synchronous caller reads its own counters back off it
when the crawl is over. ``stats.items`` is counted by the crawl instead, so an
override that forgets ``super()`` cannot corrupt it.

    uv run python examples/pipeline.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from collector import Crawler, CrawlerContext, Response, Settings, run_crawler


class JsonLines:
    """A sink: anything with the methods the crawler below calls."""

    def __init__(self, path: Path) -> None:
        self._file = path.open('w', encoding='utf-8')

    def write(self, item: Any) -> None:
        self._file.write(json.dumps(item, ensure_ascii=False) + '\n')

    def close(self) -> None:
        self._file.close()


class Quotes(Crawler):
    name = 'quotes-pipeline'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.3, max_requests=3)

    def __init__(self, ctx: CrawlerContext) -> None:
        super().__init__(ctx)
        # Run state belongs to the crawler instance, not to a module global:
        # crawl.crawler is how the caller gets it back.
        self.by_author: Counter[str] = Counter()

    async def parse(self, response: Response) -> Any:
        page = response.selector()
        for quote in page.css('div.quote'):
            yield {
                'author': quote.css('small.author::text').get(),
                'text': quote.css('span.text::text').get(),
            }

        next_page = page.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)

    async def process_item(self, item: Any) -> None:
        self.ctx.sink.write(item)
        self.by_author[item['author']] += 1
        if self.by_author[item['author']] == 3:
            await self.log(f'{item["author"]} is getting quotable')


def main() -> None:
    # Scraped text is not ASCII and a Windows console is not UTF-8 by default,
    # which is a UnicodeEncodeError the first time an author is called André.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    out = Path('quotes.jsonl')
    sink = JsonLines(out)
    try:
        crawl = run_crawler(Quotes, sink=sink)
    finally:
        sink.close()

    print(crawl.stats)
    print(f'wrote {out} ({out.stat().st_size} bytes)')
    for author, count in crawl.crawler.by_author.most_common(3):
        print(f'  {count:>2} × {author}')


if __name__ == '__main__':
    main()
