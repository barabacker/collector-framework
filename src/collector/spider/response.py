"""Response — wraps the result of ``HttpClient.request()``."""

from __future__ import annotations

import json as jsonlib
from typing import Any
from urllib.parse import urljoin

from parsel import Selector

from collector.spider.request import Request


class Response:
    """Wraps the result of HttpClient.request().

    curl_cffi's AsyncSession returns an already-materialised response
    (``.text`` / ``.status_code`` are plain attributes, not coroutines), so
    ``text`` / ``status`` are plain attributes here too.
    """

    def __init__(self, raw: Any, request: Request) -> None:
        self.request = request
        self.metadata = request.metadata
        self.status: int = raw.status_code
        self.text: str = raw.text
        self._raw = raw

    @property
    def raw(self) -> Any:
        """The underlying client response, for anything this wrapper omits."""
        return self._raw

    def selector(self) -> Selector:
        return Selector(text=self.text)

    def json(self) -> Any:
        """Parse the body as JSON."""
        return jsonlib.loads(self.text)

    def urljoin(self, href: str) -> str:
        """Resolve a link found on this page against the URL it came from."""
        return urljoin(self.request.url, href)

    def follow(
        self,
        href: str,
        *,
        method: str = 'GET',
        callback: Any = None,
        metadata: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | str | None = None,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Request:
        """Build a ``Request`` for a link on this page, resolving it first.

        ``callback`` left as None means the parser's ``parse()``, the same
        default a request built by the parser itself gets.
        """
        return Request(
            url=self.urljoin(href),
            method=method,
            callback=callback,
            metadata=metadata or {},
            headers=headers,
            data=data,
            params=params,
            json=json,
            cookies=cookies,
        )
