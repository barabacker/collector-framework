"""Datasets: items stored in order, per crawler, and exported three ways."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from collector.storage import Dataset, MemoryDataset

#: Every implementation must pass the shared tests below; Task 2 adds 'sqlite'.
KINDS = ['memory']


def _make(kind: str, tmp_path: Path) -> Dataset:
    if kind == 'memory':
        return MemoryDataset()
    raise AssertionError(kind)


@pytest.fixture(params=KINDS)
async def dataset(request, tmp_path) -> AsyncIterator[Dataset]:
    ds = _make(request.param, tmp_path)
    yield ds
    await ds.close()


async def items_of(ds: Dataset, **kwargs) -> list:
    return [item async for item in ds.iterate_items(**kwargs)]


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
