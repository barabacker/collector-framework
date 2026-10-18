"""Application-owned JSONL writer configured through Settings.

Run with uv run python examples/storage.py. Appends to quotes.jsonl.
Reading, export and the file format belong to the application.
Importing this module opens no files and makes no requests.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from collector import Crawler, Response, Settings, crawl


async def _write(file: Any, line: str) -> None:
    """Cancellation must not leave a thread writing to a closed file."""
    task = asyncio.create_task(asyncio.to_thread(file.write, line))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            task.result()
        except Exception as exc:
            cancelled.add_note(f'pending file write failed: {exc!r}')
        raise


class JsonLinesPipeline:
    """Example component; use distinct paths for concurrent crawls.

    A shared writer needs application-level ownership and locking.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> JsonLinesPipeline:
        return cls(crawler.settings.pipeline_options['jsonl_path'])

    def open_crawler(self) -> None:
        self._file = self.path.open('a', encoding='utf-8')

    async def process_item(self, item: Any) -> Any:
        line = json.dumps(item, ensure_ascii=False) + '\n'
        async with self._lock:
            await _write(self._file, line)
        return item

    async def close_crawler(self) -> None:
        await asyncio.to_thread(self._file.close)


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(
        delay=0.3,
        max_requests=2,
        pipelines=(JsonLinesPipeline,),
        pipeline_options={'jsonl_path': 'quotes.jsonl'},
    )

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
    finished = await crawl(Quotes)
    print(f'{finished.stats.items} items appended to quotes.jsonl')


def main() -> None:
    asyncio.run(run())


if __name__ == '__main__':
    main()
