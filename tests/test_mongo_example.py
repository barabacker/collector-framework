"""Only the optional Mongo driver is replaced; the example and engine are real."""

import asyncio
import sys
from types import ModuleType

import pytest
from examples.mongo import MongoPipeline
from tests.conftest import SessionHttp

from collector import Crawler, CrawlError, Settings, open_crawl


@pytest.mark.parametrize('failure_stage', [None, 'ping', 'insert', 'cancel'])
async def test_mongo_example_writes_copy_and_closes_client(monkeypatch, failure_stage):
    documents = []
    clients = []
    source = {'text': 'Привет'}
    error = OSError('Mongo unavailable')

    class Collection:
        async def insert_one(self, document):
            if failure_stage == 'insert':
                raise error
            if failure_stage == 'cancel':
                raise asyncio.CancelledError()
            document['_id'] = 'driver-generated'
            documents.append(document)

    class Database:
        def __getitem__(self, name):
            assert name == 'quotes'
            return Collection()

    class Client:
        def __init__(self, uri, **options):
            assert uri == 'mongodb://example.test:27017'
            assert options['serverSelectionTimeoutMS'] == 5000
            self.closed = False
            self.admin = self
            clients.append(self)

        def __getitem__(self, name):
            assert name == 'test_results'
            return Database()

        async def command(self, command):
            assert command == 'ping'
            if failure_stage == 'ping':
                raise error

        async def close(self):
            assert not self.closed
            self.closed = True

    driver = ModuleType('pymongo')
    driver.AsyncMongoClient = Client
    monkeypatch.setitem(sys.modules, 'pymongo', driver)
    monkeypatch.setattr('collector.engine.runner.build_http_client', lambda *a, **kw: SessionHttp())

    class Quotes(Crawler):
        name = 'quotes'
        start_urls = ['https://example.test/']
        settings = Settings(
            pipelines=(MongoPipeline,),
            pipeline_options={
                'mongo_uri': 'mongodb://example.test:27017',
                'mongo_database': 'test_results',
            },
        )

        async def parse(self, response):
            yield source

    async def consume():
        async with open_crawl(Quotes) as run:
            return [item async for item in run.stream()]

    if failure_stage == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await consume()
    elif failure_stage is not None:
        with pytest.raises(CrawlError) as caught:
            await consume()
        assert caught.value.__cause__ is error
    else:
        assert await consume() == [{'text': 'Привет'}]
        assert documents == [{'text': 'Привет', '_id': 'driver-generated'}]
    assert source == {'text': 'Привет'}
    assert len(clients) == 1
    assert clients[0].closed
