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
four packages underneath it: :mod:`collector.spider` (the parser and its two
ends of a round trip), :mod:`collector.engine` (what runs one), and
:mod:`collector.core` (the settings all three share). The fourth,
:mod:`collector.http`, is the transport — what a *hook* author writes — and it
is imported from there rather than re-exported here; the import you reach for
says which of the two you are doing.
"""

from __future__ import annotations

from collector.core import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings, clean
from collector.engine import Crawler, Stats, collect, crawl, open_crawler, run_parser
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
    'clean',
    'collect',
    'crawl',
    'open_crawler',
    'run_parser',
]
