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
