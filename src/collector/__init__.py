"""collector — a tiny async scraping framework.

Write a parser as a Spider: subclass :class:`Parser`, set ``start_urls``,
and implement ``parse()`` as an async generator that yields ``Request`` objects
to follow and items to emit. ``run_parser`` assembles the HTTP client the parser
declares (impersonation, TLS quirks, response hooks) and runs the crawl,
returning the ``Crawler`` that ran it — stats, failures and the parser itself.

Parsers are plain classes: how an application names and looks one up — a
registry, entry points, a dict — is its own business, not this package's.

The framework stores nothing and knows no item schema: override
``process_item()`` to do something with what a parser emits, or call
``collect()`` to get the items back as a list.

What this module exports is what a *parser* author writes, gathered from the
two packages underneath it that a parser touches — :mod:`collector.spider` (the
parser and its two ends of a round trip) and :mod:`collector.engine` (what runs
one) — plus :mod:`collector.settings`, which all three packages read and so
belongs to none of them. The third package, :mod:`collector.http`, is the
transport: what a *hook* author writes, imported from there rather than
re-exported here. The import you reach for says which of the two you are doing.
"""

from __future__ import annotations

from collector.engine import Crawler, Stats, collect, crawl, open_crawler, run_parser
from collector.settings import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings
from collector.spider import Parser, ParserContext, Request, Response

__version__ = '0.0.1'

__all__ = [
    #: Exported alongside RetryPolicy: narrowing or widening the retryable set
    #: is a parser's decision, and writing the default out by hand invites drift.
    'DEFAULT_RETRY_STATUSES',
    'Crawler',
    'Parser',
    'ParserContext',
    'Request',
    'Response',
    'RetryPolicy',
    'Settings',
    'Stats',
    '__version__',
    'collect',
    'crawl',
    'open_crawler',
    'run_parser',
]
