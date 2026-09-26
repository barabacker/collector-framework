"""What runs a crawler: the queue and workers, and the ways to start them.

``Crawl`` owns one run — the queue, the ``concurrency`` workers, the counters
and the failures — and ``runner`` assembles everything a run needs in the single
place it is assembled. ``params`` reads the two knobs the crawl honours out of
a job's free-form strings. ``many`` runs several crawlers at once over that same single place.
"""

from __future__ import annotations

from collector.engine.crawl import Crawl, CrawlError, Stats
from collector.engine.many import Outcome, crawl_many
from collector.engine.runner import collect, crawl, open_crawl, run_crawler

__all__ = [
    'Crawl',
    'CrawlError',
    'Outcome',
    'Stats',
    'collect',
    'crawl',
    'crawl_many',
    'open_crawl',
    'run_crawler',
]
