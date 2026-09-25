"""What runs a crawler: the queue and workers, and the ways to start them.

``Crawl`` owns one run — the queue, the ``concurrency`` workers, the counters
and the failures — and ``runner`` assembles everything a run needs in the single
place it is assembled. ``params`` reads the two knobs the crawl honours out of
a job's free-form strings.
"""

from __future__ import annotations

from collector.engine.crawl import Crawl, Stats
from collector.engine.runner import collect, crawl, open_crawl, run_crawler

__all__ = [
    'Crawl',
    'Stats',
    'collect',
    'crawl',
    'open_crawl',
    'run_crawler',
]
