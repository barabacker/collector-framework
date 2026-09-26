"""Datasets: items stored in order, per crawler, and exported three ways."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path

import pytest
from tests.conftest import items_of

from collector.storage import Dataset, MemoryDataset, SqliteDataset

#: Every implementation must pass the shared tests below; Task 2 adds 'sqlite'.
KINDS = ['memory', 'sqlite']


def _make(kind: str, tmp_path: Path) -> Dataset:
    if kind == 'memory':
        return MemoryDataset()
    if kind == 'sqlite':
        # A small batch, so the shared tests cross a batch boundary.
        return SqliteDataset(tmp_path / 'items.db', batch_size=2)
    raise AssertionError(kind)


@pytest.fixture(params=KINDS)
async def dataset(request, tmp_path) -> AsyncIterator[Dataset]:
    ds = _make(request.param, tmp_path)
    yield ds
    await ds.close()


# ── storing and reading ──────────────────────────────────────────────────────


async def test_items_come_back_in_the_order_pushed(dataset):
    for n in range(5):
        await dataset.push_data({'n': n}, crawler='a')

    assert await items_of(dataset) == [{'n': n} for n in range(5)]


async def test_items_can_be_read_for_one_crawler(dataset):
    await dataset.push_data({'n': 1}, crawler='a')
    await dataset.push_data({'n': 2}, crawler='b')
    await dataset.push_data({'n': 3}, crawler='a')

    assert await items_of(dataset, crawler='a') == [{'n': 1}, {'n': 3}]
    assert await items_of(dataset, crawler='b') == [{'n': 2}]


async def test_cyrillic_survives_a_round_trip(dataset):
    await dataset.push_data({'title': 'Лот № 5 — квартира'}, crawler='a')

    assert await items_of(dataset) == [{'title': 'Лот № 5 — квартира'}]


# ── exporting ────────────────────────────────────────────────────────────────


async def _two_lots(ds: Dataset) -> None:
    await ds.push_data({'title': 'Лот', 'price': 10}, crawler='a')
    await ds.push_data({'title': 'Дом', 'tags': ['a']}, crawler='b')


async def test_export_to_json_writes_an_array(dataset, tmp_path):
    await _two_lots(dataset)

    count = await dataset.export_to(tmp_path / 'out.json')

    assert count == 2
    written = json.loads((tmp_path / 'out.json').read_text(encoding='utf-8'))
    assert written == [{'title': 'Лот', 'price': 10}, {'title': 'Дом', 'tags': ['a']}]


async def test_export_to_jsonl_writes_one_item_per_line(dataset, tmp_path):
    await _two_lots(dataset)

    await dataset.export_to(tmp_path / 'out.jsonl')

    lines = (tmp_path / 'out.jsonl').read_text(encoding='utf-8').splitlines()
    assert [json.loads(line) for line in lines] == [
        {'title': 'Лот', 'price': 10},
        {'title': 'Дом', 'tags': ['a']},
    ]


async def test_export_to_csv_unions_the_keys_and_encodes_nested_values(dataset, tmp_path):
    await _two_lots(dataset)

    await dataset.export_to(tmp_path / 'out.csv')

    text = (tmp_path / 'out.csv').read_text(encoding='utf-8')
    assert text == 'title,price,tags\nЛот,10,\nДом,,"[""a""]"\n'


async def test_export_to_csv_puts_an_item_that_is_not_a_mapping_under_value(dataset, tmp_path):
    await dataset.push_data('plain', crawler='a')
    await dataset.push_data(3, crawler='a')

    await dataset.export_to(tmp_path / 'out.csv')

    assert (tmp_path / 'out.csv').read_text(encoding='utf-8') == 'value\nplain\n3\n'


async def test_export_can_be_limited_to_one_crawler(dataset, tmp_path):
    await _two_lots(dataset)

    assert await dataset.export_to(tmp_path / 'out.jsonl', crawler='b') == 1


async def test_an_unknown_export_format_is_refused_before_writing(dataset, tmp_path):
    await _two_lots(dataset)

    with pytest.raises(ValueError, match='.xml'):
        await dataset.export_to(tmp_path / 'out.xml')

    assert not (tmp_path / 'out.xml').exists()


async def test_a_dataset_is_an_async_context_manager(tmp_path):
    async with MemoryDataset() as ds:
        await ds.push_data({'n': 1}, crawler='a')
        assert await items_of(ds) == [{'n': 1}]


# ── SQLite only ──────────────────────────────────────────────────────────────


async def test_sqlite_stores_dates_as_iso_and_other_types_as_text(tmp_path):
    async with SqliteDataset(tmp_path / 'items.db') as ds:
        await ds.push_data(
            {'at': datetime(2026, 9, 26, 10, 0), 'day': date(2026, 9, 26), 'path': Path('x')},
            crawler='a',
        )
        assert await items_of(ds) == [
            {'at': '2026-09-26T10:00:00', 'day': '2026-09-26', 'path': 'x'}
        ]


async def test_sqlite_reads_include_a_batch_not_yet_written(tmp_path):
    async with SqliteDataset(tmp_path / 'items.db', batch_size=100) as ds:
        await ds.push_data({'n': 1}, crawler='a')
        assert await items_of(ds) == [{'n': 1}]


async def test_sqlite_flush_puts_the_batch_in_the_file(tmp_path):
    path = tmp_path / 'items.db'
    async with SqliteDataset(path, batch_size=100) as ds:
        await ds.push_data({'n': 1}, crawler='a')
        await ds.flush()

        # closing(): a sqlite3 connection's own `with` commits but does not close.
        with contextlib.closing(sqlite3.connect(path)) as other:
            rows = other.execute('SELECT crawler, item FROM items').fetchall()
        assert rows == [('a', '{"n": 1}')]


async def test_a_reopened_sqlite_file_keeps_what_was_written(tmp_path):
    path = tmp_path / 'items.db'
    async with SqliteDataset(path) as first:
        await first.push_data({'n': 1}, crawler='a')

    async with SqliteDataset(path) as second:
        await second.push_data({'n': 2}, crawler='a')
        assert await items_of(second) == [{'n': 1}, {'n': 2}]


async def test_closing_sqlite_twice_is_harmless_and_pushing_after_is_refused(tmp_path):
    ds = SqliteDataset(tmp_path / 'items.db')
    await ds.push_data({'n': 1}, crawler='a')
    await ds.close()
    await ds.close()

    with pytest.raises(RuntimeError, match='closed'):
        await ds.push_data({'n': 2}, crawler='a')


def test_a_batch_size_below_one_is_refused(tmp_path):
    with pytest.raises(ValueError, match='batch_size'):
        SqliteDataset(tmp_path / 'items.db', batch_size=0)


async def test_a_failed_write_keeps_its_batch_for_the_next_flush(tmp_path, monkeypatch):
    async with SqliteDataset(tmp_path / 'items.db', batch_size=100) as ds:
        await ds.push_data({'n': 1}, crawler='a')
        write = ds._write
        calls = 0

        def flaky(rows):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError('database is locked')
            write(rows)

        monkeypatch.setattr(ds, '_write', flaky)

        with pytest.raises(sqlite3.OperationalError):
            await ds.flush()

        assert await items_of(ds) == [{'n': 1}]


async def test_a_flush_cancelled_while_it_waits_still_writes_its_batch(tmp_path):
    async with SqliteDataset(tmp_path / 'items.db', batch_size=1) as ds:
        # Hold the SQLite thread, so the push's write has to queue behind it.
        gate = threading.Event()
        busy = asyncio.get_running_loop().run_in_executor(ds._executor, gate.wait)
        push = asyncio.create_task(ds.push_data({'n': 1}, crawler='a'))
        await asyncio.sleep(0.05)

        push.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await push
        gate.set()
        await busy

        assert await items_of(ds) == [{'n': 1}]


async def test_reading_a_closed_sqlite_dataset_says_it_is_closed(tmp_path):
    ds = SqliteDataset(tmp_path / 'items.db')
    await ds.close()

    with pytest.raises(RuntimeError, match='closed'):
        await items_of(ds)


async def test_csv_writes_booleans_as_json_does(dataset, tmp_path):
    await dataset.push_data({'sold': True, 'active': False}, crawler='a')

    await dataset.export_to(tmp_path / 'out.csv')

    assert (tmp_path / 'out.csv').read_text(encoding='utf-8') == 'sold,active\ntrue,false\n'
