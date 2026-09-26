"""collector — a tiny async scraping framework.

Subclass :class:`Crawler`, set ``start_urls``,
and implement ``parse()`` as an async generator that yields ``Request`` objects
to follow and items to emit. ``run_crawler`` assembles the HTTP client the crawler
declares (impersonation, TLS quirks, response hooks) and runs the crawl,
returning the ``Crawl`` that ran it — stats, failures and the crawler itself.

Crawlers are plain classes: how an application names and looks one up — a
registry, entry points, a dict — is its own business, not this package's.

The framework stores nothing and knows no item schema: override
``process_item()`` to do something with what a crawler emits, or call
``collect()`` to get the items back as a list.

What this module exports is what a *crawler* author writes, gathered from the
two packages underneath it that a crawler touches — :mod:`collector.crawler` (the
crawler and its two ends of a round trip) and :mod:`collector.engine` (what runs
one) — plus :mod:`collector.settings`, which all three packages read and so
belongs to none of them. The third package, :mod:`collector.http`, is the
transport: what a *hook* author writes, imported from there rather than
re-exported here. The import you reach for says which of the two you are doing.
"""

from __future__ import annotations

from collector.crawler import Crawler, CrawlerContext, Request, Response
from collector.engine import (
    Crawl,
    CrawlError,
    Outcome,
    Stats,
    collect,
    crawl,
    crawl_many,
    open_crawl,
    run_crawler,
)
from collector.settings import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings

__version__ = '0.0.1'

__all__ = [
    #: Exported alongside RetryPolicy: narrowing or widening the retryable set
    #: is a crawler's decision, and writing the default out by hand invites drift.
    'DEFAULT_RETRY_STATUSES',
    'Crawl',
    'CrawlError',
    'Crawler',
    'CrawlerContext',
    'Outcome',
    'Request',
    'Response',
    'RetryPolicy',
    'Settings',
    'Stats',
    '__version__',
    'collect',
    'crawl',
    'crawl_many',
    'open_crawl',
    'run_crawler',
]
