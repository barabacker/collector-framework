"""What a scraper writes: the parser, and the two ends of one round trip.

``Parser`` says what to fetch and what an item is; ``Request`` describes a fetch
the crawl has yet to make, and ``Response`` wraps the one it made. The three are
tightly wound — a parser builds requests, a response builds more of them — which
is why they sit together.
"""

from __future__ import annotations

from collector.spider.parser import Parser, ParserContext
from collector.spider.request import Request
from collector.spider.response import Response

__all__ = [
    'Parser',
    'ParserContext',
    'Request',
    'Response',
]
