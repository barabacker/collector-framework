"""Ordered components transform items before collect/stream sees them.

Run with uv run python examples/pipeline.py. Components are new for each crawl.
Sync methods run in the event loop; async methods are awaited.
"""

from __future__ import annotations

from typing import Any

from collector import Crawler, Response, Settings, collect


class StripText:
    def process_item(self, item: dict[str, str]) -> dict[str, str]:
        return {**item, 'text': item['text'].strip()}


class TagSource:
    @classmethod
    def from_crawler(cls, crawler: Crawler) -> TagSource:
        component = cls()
        component.source = crawler.name
        return component

    async def process_item(self, item: dict[str, str]) -> dict[str, str]:
        return {**item, 'source': self.source}


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.3, max_requests=1, pipelines=(StripText, TagSource))

    async def parse(self, response: Response) -> Any:
        for quote in response.selector().css('div.quote'):
            yield {
                'text': quote.css('span.text::text').get(),
                'author': quote.css('small.author::text').get(),
            }


def main() -> None:
    for item in collect(Quotes):
        print(item)


if __name__ == '__main__':
    main()
