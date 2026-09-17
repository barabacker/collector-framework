"""collector — a tiny async scraping framework.

Write a parser as a Spider: subclass :class:`BaseParser`, set ``start_urls``,
and implement ``parse()`` as an async generator that yields ``Request`` objects
to follow and items to emit. ``run_parser`` assembles the HTTP client the parser
declares (impersonation, TLS quirks, response hooks) and runs the crawl,
returning the ``Crawler`` that ran it — stats, failures and the parser itself.

Parsers are plain classes: how an application names and looks one up — a
registry, entry points, a dict — is its own business, not this package's.

The framework stores nothing and knows no item schema: override
``process_item()`` to do something with what a parser emits, or call
``collect()`` to get the items back as a list.
"""

from __future__ import annotations

from collector.crawler import Crawler, Stats
from collector.http import (
    HttpClient,
    Middleware,
    RequestHook,
    ResponseHook,
    Throttle,
    build_http_client,
    ca_bundle_with_extra_cert,
)
from collector.params import read_concurrency, read_flag, read_max_pages, read_max_requests
from collector.parser import BaseParser, ParserContext
from collector.request import Request
from collector.response import Response
from collector.runner import collect, crawl, open_crawler, run_parser
from collector.settings import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings
from collector.text import clean

__version__ = '0.0.1'

__all__ = [
    'DEFAULT_RETRY_STATUSES',
    'BaseParser',
    'Crawler',
    'HttpClient',
    'Middleware',
    'ParserContext',
    'Request',
    'RequestHook',
    'Response',
    'ResponseHook',
    'RetryPolicy',
    'Settings',
    'Stats',
    'Throttle',
    '__version__',
    'build_http_client',
    'ca_bundle_with_extra_cert',
    'clean',
    'collect',
    'crawl',
    'open_crawler',
    'read_concurrency',
    'read_flag',
    'read_max_pages',
    'read_max_requests',
    'run_parser',
]
