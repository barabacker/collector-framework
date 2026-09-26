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
        #: Rows whose write failed, kept on the SQLite thread and written first
        #: next time — so a failure neither loses them nor lets later rows
        #: overtake them. Touched only on that thread.
        self._unwritten: list[tuple[str, str, str]] = []
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
        if not self._pending and not self._unwritten:
            return
        # Swapped out before the await, so pushes that land meanwhile start a new batch.
        rows, self._pending = self._pending, []
        write = self._submit(self._write, rows)
        # A caller cancelled while the write waits for the thread must not take
        # the write with it (shield), nor leave its failure unretrieved: the
        # rows stay in _unwritten either way, for the next flush or close().
        write.add_done_callback(_retrieve)
        await asyncio.shield(write)

    async def iterate_items(self, *, crawler: str | None = None) -> AsyncIterator[Any]:
        if self._closed:
            raise RuntimeError(f'dataset {self.path} is closed')
        await self.flush()
        after = 0
        while rows := await self._submit(self._read, crawler, after):
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
            await self._submit(self._disconnect)
            self._executor.shutdown(wait=False)

    # ── on the SQLite thread ────────────────────────────────────────────────

    def _submit(self, fn: Callable[..., Any], *args: Any) -> asyncio.Future[Any]:
        return asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            connection = sqlite3.connect(self.path)
            # WAL lets the sqlite3 shell read the file while a crawl writes to it.
            connection.execute('PRAGMA journal_mode=WAL')
            # With WAL, NORMAL still survives the process dying; it only stops
            # an fsync on every batch — which, with many crawls sharing one
            # file, is most of what writing would otherwise cost.
            connection.execute('PRAGMA synchronous=NORMAL')
            connection.execute(_SCHEMA)
            connection.execute(_INDEX)
            connection.commit()
            self._connection = connection
        return self._connection

    def _write(self, rows: list[tuple[str, str, str]]) -> None:
        batch = self._unwritten + rows
        connection = self._connect()
        try:
            with connection:  # one transaction per batch
                connection.executemany(_INSERT, batch)
        except Exception:
            self._unwritten = batch
            raise
        self._unwritten = []

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


def _retrieve(write: asyncio.Future[Any]) -> None:
    """Mark a write's failure seen: its rows are kept for the next write, and the
    caller — if still there — gets the exception from ``await``."""
    if not write.cancelled():
        write.exception()
