"""Shared fakes: a response object and an HTTP client that never leaves the process."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from collector.crawler import CrawlerContext


@dataclass
class FakeResponse:
    """The parts of a curl_cffi response the framework touches."""

    text: str = ''
    status_code: int = 200
    url: str = 'https://example.test/'
    content: bytes = b''
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeHttp:
    """Stands in for HttpClient: serves canned bodies and records the calls."""

    pages: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    #: Keyword arguments of each call, positionally matching ``calls`` — what a
    #: Request actually handed the transport.
    sent: list[dict[str, Any]] = field(default_factory=list)
    default_body: str = ''

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        self.sent.append(kwargs)
        return FakeResponse(text=self.pages.get(url, self.default_body), url=url)


@pytest.fixture
def ctx_factory():
    """Build a CrawlerContext around a FakeHttp, capturing log lines."""

    def make(http: FakeHttp, **kwargs: Any) -> tuple[CrawlerContext, list[str]]:
        lines: list[str] = []

        async def log(message: str) -> None:
            lines.append(message)

        return CrawlerContext(http=http, log=log, **kwargs), lines

    return make
