"""Application-owned MongoDB pipeline using optional PyMongo Async.

Run manually with: uv run --with 'pymongo>=4.13' python examples/mongo.py
MONGO_URI, MONGO_DATABASE and MONGO_COLLECTION override the sample settings.
This example appends documents; it does not upsert, migrate or export data.
Importing it requires no PyMongo installation and opens no connections.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from collector import Crawler, Response, Settings, crawl


class MongoPipeline:
    """One async client per crawl; accepts mapping items with BSON-compatible values."""

    def __init__(self, mongo_uri: str, mongo_database: str, collection_name: str) -> None:
        self.mongo_uri = mongo_uri
        self.mongo_database = mongo_database
        self.collection_name = collection_name

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> MongoPipeline:
        options = crawler.settings.pipeline_options
        return cls(
            mongo_uri=options['mongo_uri'],
            mongo_database=options.get('mongo_database', 'collector_examples'),
            collection_name=options.get('mongo_collection', crawler.name),
        )

    async def open_crawler(self) -> None:
        # Optional application dependency; never imported by collector itself.
        from pymongo import AsyncMongoClient

        self.client = AsyncMongoClient(
            self.mongo_uri, serverSelectionTimeoutMS=5000, retryWrites=False
        )
        try:
            # Fail before scraping if the database cannot be reached.
            await self.client.admin.command('ping')
            self.collection = self.client[self.mongo_database][self.collection_name]
        except BaseException:
            # The engine closes only components whose open completed successfully.
            await self.client.close()
            raise

    async def process_item(self, item: Mapping[str, Any]) -> Mapping[str, Any]:
        # PyMongo adds _id to the inserted document. Keep the streamed item unchanged.
        await self.collection.insert_one(dict(item))
        return item

    async def close_crawler(self) -> None:
        await self.client.close()


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(
        delay=0.3,
        max_requests=1,
        pipelines=(MongoPipeline,),
        pipeline_options={
            'mongo_uri': 'mongodb://localhost:27017',
            'mongo_database': 'collector_examples',
        },
    )

    async def parse(self, response: Response) -> Any:
        for quote in response.selector().css('div.quote'):
            yield {
                'author': quote.css('small.author::text').get(),
                'text': quote.css('span.text::text').get(),
                'tags': quote.css('div.tags a.tag::text').getall(),
            }


async def run() -> None:
    finished = await crawl(Quotes)
    print(f'{finished.stats.items} documents inserted')


def main() -> None:
    options = dict(Quotes.settings.pipeline_options)
    for key in ('mongo_uri', 'mongo_database', 'mongo_collection'):
        value = os.environ.get(key.upper())
        if value is not None:
            options[key] = value
    Quotes.settings = replace(Quotes.settings, pipeline_options=options)
    asyncio.run(run())


if __name__ == '__main__':
    main()
