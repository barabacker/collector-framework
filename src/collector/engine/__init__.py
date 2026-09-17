"""What runs a parser: the queue and workers, and the ways to start them.

``Crawler`` owns one run — the queue, the ``concurrency`` workers, the counters
and the failures — and ``runner`` assembles everything a run needs in the single
place it is assembled. ``params`` reads the two knobs the crawler honours out of
a job's free-form strings.
"""

from __future__ import annotations

from collector.engine.crawler import Crawler, Stats
from collector.engine.runner import collect, crawl, open_crawler, run_parser

__all__ = [
    'Crawler',
    'Stats',
    'collect',
    'crawl',
    'open_crawler',
    'run_parser',
]
