"""Shared fakes: a response object and an HTTP client that never leaves the process."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from collector.crawler import CrawlerContext
from collector.testing import FakeHttp as StrictFakeHttp
from collector.testing import FakeResponse


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
        transport = StrictFakeHttp({(method, url): self.pages.get(url, self.default_body)})
        transport.calls = self.calls
        transport.sent = self.sent
        return await transport.request(method, url, **kwargs)


class SessionHttp(FakeHttp):
    """FakeHttp that can stand where ``open_crawl()`` expects a session.

    A subclass rather than patching ``__aenter__`` onto ``FakeHttp`` itself,
    which would leak into every other test that uses the plain fake.
    """

    async def __aenter__(self) -> SessionHttp:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


@pytest.fixture
def ctx_factory():
    """Build a CrawlerContext around a FakeHttp, capturing log lines."""

    def make(http: FakeHttp, **kwargs: Any) -> tuple[CrawlerContext, list[str]]:
        lines: list[str] = []

        async def log(message: str) -> None:
            lines.append(message)

        return CrawlerContext(http=http, log=log, **kwargs), lines

    return make
