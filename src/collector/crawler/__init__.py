"""What a scraper writes: the crawler, and the two ends of one round trip.

``Crawler`` says what to fetch and what an item is; ``Request`` describes a fetch
the crawl has yet to make, and ``Response`` wraps the one it made. The three are
tightly wound — a crawler builds requests, a response builds more of them — which
is why they sit together.
"""

from __future__ import annotations

from collector.crawler.crawler import Crawler, CrawlerContext
from collector.crawler.request import Request, request_key
from collector.crawler.response import Response

__all__ = [
    'Crawler',
    'CrawlerContext',
    'Request',
    'Response',
    'request_key',
]
