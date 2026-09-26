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
