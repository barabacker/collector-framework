# Dataset Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pass `dataset=` to a run and every item the crawl emits is stored under its crawler's name, in a `MemoryDataset` or a `SqliteDataset`, readable with `iterate_items()` and exportable with `export_to('x.json' | 'x.jsonl' | 'x.csv')`.

**Architecture:** A new package `collector.storage`: an abstract `Dataset` (with `export_to` implemented once over `iterate_items`), an in-memory implementation, and a SQLite one that does all its work on one dedicated thread and writes in batches. The engine takes the dataset as a run parameter: `Crawl` pushes each item after `process_item()` and flushes the dataset when the crawl ends.

**Tech Stack:** Python ≥ 3.11 (stdlib `sqlite3`, `csv`, `json`, `concurrent.futures`), pytest (+ pytest-asyncio, `asyncio_mode = "auto"`), ruff (single quotes, line length 100), uv.

**Spec:** `docs/superpowers/specs/2026-09-26-dataset-storage-design.md`

**Conventions** (every task):
- Single quotes; `from __future__ import annotations` at the top of every module.
- Test names are sentences. Docstrings and comments explain *why*, at the density of the surrounding files.
- Run everything through `uv run`. Baseline: `uv run pytest -q` → `302 passed, 15 deselected`.
- Lint gate after every task: `uv run ruff check . && uv run ruff format --check .`; if format complains about a file you touched, `uv run ruff format <file>`.
- Commit messages: imperative, capitalised, no prefix, a blank line, then a `Co-Authored-By:` line for the model writing the commit.
- Code fragments that are not complete Python (a parameter line, a line to replace) are fenced as `text` in this plan on purpose — ruff reformats `python` blocks in Markdown and would mangle them. Apply them exactly as shown.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `src/collector/storage/__init__.py` | create | package exports |
| `src/collector/storage/base.py` | create | `Dataset`, JSON encoding, the three export formats |
| `src/collector/storage/memory.py` | create | `MemoryDataset` |
| `src/collector/storage/sqlite.py` | create | `SqliteDataset` |
| `src/collector/engine/crawl.py` | modify | `Crawl.dataset`: push per item, flush at the end |
| `src/collector/engine/runner.py`, `src/collector/engine/many.py` | modify | `dataset=` on every entry point |
| `src/collector/__init__.py`, `tests/test_package.py` | modify | root exports |
| `tests/test_storage.py` | create | both datasets |
| `tests/test_crawl.py`, `tests/test_many.py`, `tests/test_runner.py` | modify | the engine side |
| `examples/storage.py`, `examples/README.md`, `README.md`, `CHANGELOG.md`, `.gitignore`, `examples/pipeline.py`, `src/collector/crawler/crawler.py` | create/modify | documentation |

---

### Task 1: `Dataset`, export, and `MemoryDataset`

**Files:**
- Create: `src/collector/storage/__init__.py`, `src/collector/storage/base.py`, `src/collector/storage/memory.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_storage.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'collector.storage'`.

- [ ] **Step 3: Implement**

Create `src/collector/storage/base.py`:

```python
"""Where a crawl's items are kept: the interface, and exporting from it.

A ``Dataset`` only appends. Each item is stored with the name of the crawler
that emitted it, so one dataset can take a whole ``crawl_many()`` run and still
be read one site at a time. What it is stored in is the implementation's
business; exporting is written once, here, over ``iterate_items()``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Self


def encode(value: Any) -> str:
    """JSON for an item: dates in ISO 8601, anything else JSON lacks via ``str()``.

    Refusing a ``datetime`` would make a dataset fail on exactly the models it
    is meant to show — a parsed lot carries its deadlines.
    """
    return json.dumps(value, ensure_ascii=False, default=_fallback)


def _fallback(value: Any) -> str:
    if isinstance(value, date):  # a datetime is a date too
        return value.isoformat()
    return str(value)


class Dataset(ABC):
    """An append-only store of items, each tagged with the crawler that emitted it."""

    @abstractmethod
    async def push_data(self, item: Any, *, crawler: str) -> None:
        """Store one item emitted by ``crawler`` (its ``Crawler.name``)."""

    @abstractmethod
    def iterate_items(self, *, crawler: str | None = None) -> AsyncIterator[Any]:
        """Yield the stored items in the order they were pushed — all, or one crawler's."""

    async def flush(self) -> None:  # noqa: B027 — optional hook
        """Make whatever is buffered durable. Nothing to do by default."""

    async def close(self) -> None:
        """Release the backend. Flushes by default."""
        await self.flush()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def export_to(self, path: str | Path, *, crawler: str | None = None) -> int:
        """Write the items to ``path`` in the format its extension names; return how many.

        ``.json`` is one array, ``.jsonl`` one item per line, ``.csv`` one row
        per item. Any other extension is refused before anything is written.
        """
        path = Path(path)
        render = _FORMATS.get(path.suffix.lower())
        if render is None:
            known = ', '.join(sorted(_FORMATS))
            raise ValueError(f'cannot export to {path.suffix or path.name!r}; known: {known}')
        items = [item async for item in self.iterate_items(crawler=crawler)]
        await asyncio.to_thread(path.write_text, render(items), encoding='utf-8', newline='')
        return len(items)


def _to_json(items: list[Any]) -> str:
    return json.dumps(items, ensure_ascii=False, default=_fallback, indent=2) + '\n'


def _to_jsonl(items: list[Any]) -> str:
    return ''.join(encode(item) + '\n' for item in items)


def _to_csv(items: list[Any]) -> str:
    """One row per item; the columns are every top-level key, in the order first seen."""
    rows = [
        {str(key): value for key, value in item.items()}
        if isinstance(item, Mapping)
        else {'value': item}
        for item in items
    ]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(value) for key, value in row.items()})
    return buffer.getvalue()


def _cell(value: Any) -> Any:
    """A CSV cell: scalars as they are, anything nested as its JSON."""
    if value is None:
        return ''
    if isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, date):
        return value.isoformat()
    return encode(value)


_FORMATS: dict[str, Callable[[list[Any]], str]] = {
    '.json': _to_json,
    '.jsonl': _to_jsonl,
    '.csv': _to_csv,
}
```

Create `src/collector/storage/memory.py`:

```python
"""A dataset in a list: for tests, scripts and anything that ends with the process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from collector.storage.base import Dataset


class MemoryDataset(Dataset):
    """Keeps the items as they were emitted — no JSON round trip, the same objects back."""

    def __init__(self) -> None:
        self._records: list[tuple[str, Any]] = []

    async def push_data(self, item: Any, *, crawler: str) -> None:
        self._records.append((crawler, item))

    async def iterate_items(self, *, crawler: str | None = None) -> AsyncIterator[Any]:
        # Over a snapshot, so reading while a crawl is still pushing is safe.
        for name, item in list(self._records):
            if crawler is None or name == crawler:
                yield item
```

Create `src/collector/storage/__init__.py`:

```python
"""Keeping what a crawl emits: an append-only ``Dataset`` and two implementations.

The framework stores nothing unless a run is given a dataset; then every item
the crawl emits is pushed to it under the crawler's name. ``MemoryDataset``
holds them for the life of the process, ``SqliteDataset`` in a file that can be
queried while a crawl is still writing. An application with its own store
implements ``Dataset``.
"""

from __future__ import annotations

from collector.storage.base import Dataset
from collector.storage.memory import MemoryDataset

__all__ = ['Dataset', 'MemoryDataset']
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_storage.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/storage tests/test_storage.py
git commit -m "Add Dataset, its exports, and an in-memory implementation"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 2: `SqliteDataset`

**Files:**
- Create: `src/collector/storage/sqlite.py`
- Modify: `src/collector/storage/__init__.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_storage.py`:

1. Add imports: `import contextlib` before `import json`, `import sqlite3` after `import json`; `from datetime import date, datetime` after `from collections.abc import AsyncIterator`; and change `from collector.storage import Dataset, MemoryDataset` to `from collector.storage import Dataset, MemoryDataset, SqliteDataset`.

2. Replace

```text
KINDS = ['memory']
```

with

```text
KINDS = ['memory', 'sqlite']
```

and in `_make`, before `raise AssertionError(kind)`, add:

```text
    if kind == 'sqlite':
        # A small batch, so the shared tests cross a batch boundary.
        return SqliteDataset(tmp_path / 'items.db', batch_size=2)
```

3. Append:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_storage.py -q`
Expected: collection error — `ImportError: cannot import name 'SqliteDataset'`.

- [ ] **Step 3: Implement**

Create `src/collector/storage/sqlite.py`:

```python
"""A dataset in a SQLite file: for looking at what a crawl collected.

SQLite is in the standard library, an insert does not get dearer as the file
grows — the reason TinyDB, which rewrites its whole file on every insert, was
not used — and the file can be opened in the ``sqlite3`` shell while a crawl
is still writing to it. Items are stored as JSON, one row each, with the
crawler's name and the time they were pushed beside them; the framework still
knows no item schema.

    SELECT crawler, count(*) FROM items GROUP BY crawler;
    SELECT json_extract(item, '$.price') FROM items WHERE crawler = 'centerr';
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collector.storage.base import Dataset, encode

_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS items ('
    'id INTEGER PRIMARY KEY, crawler TEXT NOT NULL, stored_at TEXT NOT NULL, item TEXT NOT NULL)'
)
_INDEX = 'CREATE INDEX IF NOT EXISTS items_crawler ON items (crawler)'
_INSERT = 'INSERT INTO items (crawler, stored_at, item) VALUES (?, ?, ?)'
#: Rows read per round trip to the SQLite thread while iterating.
_PAGE = 1000


class SqliteDataset(Dataset):
    """Items in a SQLite file, appended to across runs.

    Every call into SQLite runs on one thread owned by the dataset: a
    ``sqlite3`` connection may not move between threads, and the event loop —
    which every concurrent crawl shares — must not wait on the disk. Pushes are
    buffered and written ``batch_size`` at a time in one transaction; a crawl
    flushes the rest when it ends, and ``close()`` does too.
    """

    def __init__(self, path: str | Path, *, batch_size: int = 100) -> None:
        if batch_size < 1:
            raise ValueError(f'batch_size must be at least 1, got {batch_size}')
        self.path = Path(path)
        self.batch_size = batch_size
        self._pending: list[tuple[str, str, str]] = []
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sqlite-dataset')
        self._connection: sqlite3.Connection | None = None
        self._closed = False

    async def push_data(self, item: Any, *, crawler: str) -> None:
        if self._closed:
            raise RuntimeError(f'dataset {self.path} is closed')
        self._pending.append((crawler, datetime.now(UTC).isoformat(), encode(item)))
        if len(self._pending) >= self.batch_size:
            await self.flush()

    async def flush(self) -> None:
        if not self._pending:
            return
        # Swapped out before the await, so pushes that land meanwhile start a new batch.
        rows, self._pending = self._pending, []
        await self._call(self._write, rows)

    async def iterate_items(self, *, crawler: str | None = None) -> AsyncIterator[Any]:
        await self.flush()
        after = 0
        while rows := await self._call(self._read, crawler, after):
            for _, item in rows:
                yield json.loads(item)
            after = rows[-1][0]

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await self.flush()
        finally:
            self._closed = True
            await self._call(self._disconnect)
            self._executor.shutdown(wait=False)

    # ── on the SQLite thread ────────────────────────────────────────────────

    async def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            connection = sqlite3.connect(self.path)
            # WAL lets the sqlite3 shell read the file while a crawl writes to it.
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute(_SCHEMA)
            connection.execute(_INDEX)
            connection.commit()
            self._connection = connection
        return self._connection

    def _write(self, rows: list[tuple[str, str, str]]) -> None:
        connection = self._connect()
        with connection:  # one transaction per batch
            connection.executemany(_INSERT, rows)

    def _read(self, crawler: str | None, after: int) -> list[tuple[int, str]]:
        connection = self._connect()
        if crawler is None:
            cursor = connection.execute(
                'SELECT id, item FROM items WHERE id > ? ORDER BY id LIMIT ?', (after, _PAGE)
            )
        else:
            cursor = connection.execute(
                'SELECT id, item FROM items WHERE id > ? AND crawler = ? ORDER BY id LIMIT ?',
                (after, crawler, _PAGE),
            )
        return cursor.fetchall()

    def _disconnect(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
```

In `src/collector/storage/__init__.py`, add `from collector.storage.sqlite import SqliteDataset` after the `memory` import and change `__all__` to `['Dataset', 'MemoryDataset', 'SqliteDataset']`.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_storage.py -q` — all pass, including every shared test for `sqlite`.
Also run: `uv run pytest tests/test_storage.py -q -W error` — no `ResourceWarning` about unclosed connections or threads. If there is one, find which test leaks it rather than silencing the warning.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/storage tests/test_storage.py
git commit -m "Add SqliteDataset: items in a file you can query mid-crawl"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 3: A run writes its items to the dataset

**Files:**
- Modify: `src/collector/engine/crawl.py`, `src/collector/engine/runner.py`, `src/collector/engine/many.py`
- Test: `tests/test_crawl.py`, `tests/test_runner.py`, `tests/test_many.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_crawl.py`, add `from collector.storage import MemoryDataset` after the `from collector import ...` line, and append:

```python
# ── datasets ─────────────────────────────────────────────────────────────────


async def test_a_crawl_stores_every_item_under_the_crawlers_name(ctx_factory):
    dataset = MemoryDataset()
    ctx, _ = ctx_factory(FakeHttp())

    await Crawl(_TwoPages(ctx), dataset=dataset).run()

    stored = [item['url'] async for item in dataset.iterate_items(crawler='two_pages')]
    assert stored == [PAGE_1, PAGE_2]


async def test_the_dataset_is_flushed_once_when_the_crawl_ends(ctx_factory):
    class _Counting(MemoryDataset):
        flushes = 0

        async def flush(self) -> None:
            self.flushes += 1

    dataset = _Counting()
    ctx, _ = ctx_factory(FakeHttp())

    await Crawl(_TwoPages(ctx), dataset=dataset).run()

    assert dataset.flushes == 1


async def test_a_dataset_that_refuses_an_item_fails_that_request(ctx_factory):
    class _Full(MemoryDataset):
        async def push_data(self, item: Any, *, crawler: str) -> None:
            raise OSError('disk full')

    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_TwoPages(ctx), dataset=_Full())

    with pytest.raises(OSError, match='disk full'):
        await crawl.run()

    # Page 1's item fails before its callback gets to the link to page 2, so
    # one request, one failure.
    assert len(crawl.errors) == 1


async def test_a_streamed_crawl_stores_the_items_it_streams(ctx_factory):
    dataset = MemoryDataset()
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_TwoPages(ctx), dataset=dataset)

    streamed = [item async for item in crawl.stream()]

    assert streamed == [item async for item in dataset.iterate_items()]
```

In `tests/test_runner.py`, add `from collector.storage import MemoryDataset` after the `from collector import ...` line, and append:

```python
def test_run_crawler_passes_a_dataset_through(monkeypatch):
    _patch_client(monkeypatch)
    dataset = MemoryDataset()

    run_crawler(_Counting, sink=[], dataset=dataset)

    assert asyncio.run(_items(dataset)) == [{'url': URL}]


async def _items(dataset: MemoryDataset) -> list[Any]:
    return [item async for item in dataset.iterate_items()]
```

(`asyncio` and `Any` are already imported in `tests/test_runner.py`.)

In `tests/test_many.py`, add `from collector.storage import MemoryDataset` after the `from collector import ...` line, and append:

```python
async def test_one_dataset_keeps_each_crawlers_items_apart(monkeypatch):
    _patch_client(monkeypatch)
    dataset = MemoryDataset()

    await drain([_crawler('a'), _crawler('b')], dataset=dataset)

    assert [item async for item in dataset.iterate_items(crawler='a')] == [{'site': 'a'}]
    assert [item async for item in dataset.iterate_items(crawler='b')] == [{'site': 'b'}]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_crawl.py tests/test_runner.py tests/test_many.py -q -k dataset`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'dataset'`.

- [ ] **Step 3: Implement**

**`src/collector/engine/crawl.py`**

1. In the `if TYPE_CHECKING:` block, add:

```text
    from collector.storage.base import Dataset
```

2. In `Crawl`, after the `errors` field and before `_seen`, add:

```text
    #: Where every item this crawl emits is also written, under the crawler's
    #: name; the caller owns it — opens and closes it — and may share it.
    dataset: Dataset | None = None
```

3. In `run()`'s `finally`, replace

```text
            self.stats.finished_at = time.monotonic()
            # Always, regardless of how the crawl ended, and before errors are
            # raised below — a crawler that opened something in __init__ still
            # needs it closed even when the crawl itself is about to fail.
            await crawler.closed(self.stats)
```

with

```text
            self.stats.finished_at = time.monotonic()
            try:
                if self.dataset is not None:
                    # A batch still buffered is written even by a caller that
                    # never closes the dataset — the engine does not own it.
                    await self.dataset.flush()
            finally:
                # Always, regardless of how the crawl ended, and before errors
                # are raised below — a crawler that opened something in
                # __init__ still needs it closed even when the crawl fails.
                await crawler.closed(self.stats)
```

4. In `_handle()`, replace

```text
                await crawler.process_item(result)
                if self._out is not None:
```

with

```text
                await crawler.process_item(result)
                if self.dataset is not None:
                    await self.dataset.push_data(result, crawler=crawler.name)
                if self._out is not None:
```

**`src/collector/engine/runner.py`**

1. In the `TYPE_CHECKING`-free imports, add `from collector.storage.base import Dataset` after `from collector.engine.crawl import Crawl, CrawlError` (`storage` imports nothing from `engine`, so there is no cycle).

2. On each of `open_crawl`, `crawl` and `run_crawler`, add after the `sink` parameter:

```text
    dataset: Dataset | None = None,
```

and on `collect`, after the `params` parameter, the same line.

3. In `open_crawl`, replace

```text
        crawl = Crawl(crawler_cls(CrawlerContext(http=http, params=params, sink=sink, log=log)))
```

with

```text
        crawler = crawler_cls(CrawlerContext(http=http, params=params, sink=sink, log=log))
        crawl = Crawl(crawler, dataset=dataset)
```

4. Forward it: in `crawl`, `open_crawl(crawler_cls, params=params, sink=sink, dataset=dataset, log=log)`; in `run_crawler`, `crawl(crawler_cls, params=params, sink=sink, dataset=dataset, log=log or _default_log(crawler_cls))`; in `collect`, add `dataset=dataset,` to its `open_crawl(...)` call.

5. Add one sentence to `open_crawl`'s docstring, at the end of its first paragraph: ``A ``dataset``, if given, gets every item the crawl emits; the caller owns it.``

**`src/collector/engine/many.py`**

1. Add `from collector.storage.base import Dataset` after `from collector.engine.runner import open_crawl`.
2. Add to `crawl_many`'s parameters, after `params`:

```text
    dataset: Dataset | None = None,
```

and to its docstring, after the sentence about `params`: ``One ``dataset``, if given, takes every crawler's items, each under its name.``
3. Pass it: `_run_one(crawler_cls, gate, params, dataset, consume, log)`, add `dataset: Dataset | None,` to `_run_one`'s parameters after `params`, and add `dataset=dataset,` to its `open_crawl(...)` call.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_crawl.py tests/test_runner.py tests/test_many.py -q` — all pass.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/engine tests/test_crawl.py tests/test_runner.py tests/test_many.py
git commit -m "Write a run's items to a dataset when one is given"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 4: Exports, example and documentation

**Files:**
- Modify: `src/collector/__init__.py`, `tests/test_package.py`
- Create: `examples/storage.py`
- Modify: `examples/README.md`, `README.md`, `CHANGELOG.md`, `.gitignore`, `examples/pipeline.py`, `src/collector/crawler/crawler.py`

- [ ] **Step 1: Root exports**

In `tests/test_package.py`, add to the expected set after `'crawl_many',`:

```text
        'Dataset',
        'MemoryDataset',
        'SqliteDataset',
```

Run `uv run pytest tests/test_package.py -q` — FAIL.

In `src/collector/__init__.py`, add `from collector.storage import Dataset, MemoryDataset, SqliteDataset` after the `from collector.settings import ...` line, and insert `'Dataset',` after `'CrawlerContext',`, `'MemoryDataset',` after `'Dataset',`, and `'SqliteDataset',` after `'Settings',` in `__all__`, so the capitalised names stay sorted. Replace the docstring paragraph

```text
The framework stores nothing and knows no item schema: override
``process_item()`` to do something with what a crawler emits, or call
``collect()`` to get the items back as a list.
```

with

```text
The framework knows no item schema and stores nothing unless a run is given a
``Dataset``: pass one to keep every item, override ``process_item()`` to do
something else with what a crawler emits, or call ``collect()`` to get the
items back as a list.
```

Run `uv run pytest tests/test_package.py -q` — pass.

- [ ] **Step 2: The other "stores nothing" sentences**

In `src/collector/crawler/crawler.py`, in `process_item`'s docstring, replace `The framework stores nothing: an application overrides this to write the` with `Without a dataset the framework stores nothing: an application overrides this to write the` and re-wrap that paragraph to line length.

In `examples/pipeline.py`, replace

```text
The framework stores nothing and knows no item schema. An application overrides
```

with

```text
The framework knows no item schema, and without a ``Dataset`` stores nothing (see
``storage.py`` for that). An application overrides
```

and re-wrap the paragraph.

- [ ] **Step 3: The example**

Create `examples/storage.py`:

```python
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
        first = [item async for item in dataset.iterate_items()][:1]

    print(f'{finished.stats.items} items stored in quotes.db, {exported} exported to quotes.csv')
    if first:
        print(f'  first: {first[0]["author"]} — {first[0]["text"][:50]}…')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(run())


if __name__ == '__main__':
    main()
```

In `.gitignore`, under the `# This project` block, change the comment line to `#   Examples write their items to the working directory when run.` and add after `quotes.jsonl`:

```text
quotes.db
quotes.db-wal
quotes.db-shm
quotes.csv
```

In `examples/README.md`: change `Nine runnable scripts` to `Ten runnable scripts`; add after the `pipeline.py` row:

```text
| [`storage.py`](storage.py) | `dataset=`: every item a crawl emits kept in a `SqliteDataset` you can query mid-crawl, then exported to CSV. |
```

and in the "They use the network" paragraph add `storage.py` to the quotes.toscrape.com list (after `many.py`), and change the last sentence `` `pipeline.py` writes `quotes.jsonl` into the working directory. `` to `` `pipeline.py` writes `quotes.jsonl`, and `storage.py` `quotes.db` and `quotes.csv`, into the working directory. ``

Run: `uv run pytest tests/test_examples.py -q` — all pass.
Run (network): `uv run python examples/storage.py` — expect `20 items stored in quotes.db, 20 exported to quotes.csv` and a first quote. If quotes.toscrape.com is unreachable, say so in the report.

- [ ] **Step 4: README and CHANGELOG**

In `README.md`, "## What you do not get, by design": remove `no storage, ` from the first sentence, and replace

```text
The framework
never persists anything: override `process_item()` and write to `ctx.sink`,
which it passes through untouched.
```

with

```text
Beyond a `Dataset` you pass
in, the framework persists nothing: override `process_item()` and write to
`ctx.sink`, which it passes through untouched.
```

(re-wrap that paragraph to the surrounding width). Then, directly after the "De-duplication" bullet, add:

```markdown
- **Storage** — pass `dataset=` to any entry point (`crawl_many` included) and
  every item the crawl emits is also written there, under the crawler's name.
  `MemoryDataset` keeps them in a list; `SqliteDataset('run.db')` in a file you
  can query from the `sqlite3` shell while the crawl runs, written in batches
  from its own thread. `iterate_items(crawler=...)` reads them back and
  `export_to('x.json' | 'x.jsonl' | 'x.csv')` writes them out. The caller owns
  the dataset; implement `Dataset` for any other store.
```

In `CHANGELOG.md`, under `## [Unreleased]` → `### Added`, add as the first bullet:

```markdown
- `collector.storage`: `Dataset` — append-only, each item tagged with the
  crawler that emitted it, `push_data` / `iterate_items(crawler=)` / `flush` /
  `close`, and `export_to()` for `.json`, `.jsonl` and `.csv` — with
  `MemoryDataset` and `SqliteDataset` (stdlib `sqlite3`, one dedicated thread,
  batched inserts, WAL). `open_crawl()`, `crawl()`, `run_crawler()`,
  `collect()` and `crawl_many()` take `dataset=`: every emitted item is pushed
  after `process_item()`, and the dataset is flushed when the crawl ends. The
  caller owns it. Modelled on Crawlee's `Dataset`; TinyDB was ruled out because
  it rewrites its whole file on every insert.
```

- [ ] **Step 5: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/__init__.py src/collector/crawler/crawler.py tests/test_package.py examples/storage.py examples/README.md examples/pipeline.py README.md CHANGELOG.md .gitignore
git commit -m "Export and document datasets, with an example that keeps quotes in SQLite"
```
(with the blank line and `Co-Authored-By:` line.)
