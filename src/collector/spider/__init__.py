"""Spider-style parser building blocks: Request / Response / ParserContext / BaseParser."""

from __future__ import annotations

from collector.spider.context import ParserContext
from collector.spider.parser import BaseParser
from collector.spider.request import Request
from collector.spider.response import Response

__all__ = ['BaseParser', 'ParserContext', 'Request', 'Response']
