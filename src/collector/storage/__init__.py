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
from collector.storage.sqlite import SqliteDataset

__all__ = ['Dataset', 'MemoryDataset', 'SqliteDataset']
