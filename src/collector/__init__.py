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

What this module exports is what a *parser* author writes. The transport — the
client, its hooks, the CA-bundle helper — lives in
:mod:`collector.http`, which is what a *hook* author writes; the import you
reach for says which of the two you are doing.
"""

from __future__ import annotations

from collector.crawler import Crawler, Stats
from collector.parser import Parser, ParserContext
from collector.request import Request
from collector.response import Response
from collector.runner import collect, crawl, open_crawler, run_parser
from collector.settings import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings
from collector.text import clean

__version__ = '0.0.1'

__all__ = [
    #: Exported alongside RetryPolicy: narrowing or widening the retryable set
    #: is a parser's decision, and writing the default out by hand invites drift.
    'DEFAULT_RETRY_STATUSES',
    'Parser',
    'Crawler',
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
