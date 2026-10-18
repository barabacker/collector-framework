"""Offline fixtures for crawler tests; no test runner dependency."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from curl_cffi.requests.headers import Headers


class FakeResponse:
    """A materialised raw response, with UTF-8 content unless explicitly supplied."""

    def __init__(
        self,
        text: str = '',
        status_code: int = 200,
        url: str = '',
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.url = url
        self.content = text.encode('utf-8') if content is None else content
        self.headers = Headers(headers)


class FakeHttp:
    """Serve explicit (method, literal URL) fixtures and record every attempt.

    Missing routes raise LookupError. This client does not simulate HTTP hooks,
    retries, throttling or network behaviour. Its caller owns its lifetime.
    """

    def __init__(
        self,
        pages: Mapping[tuple[str, str], str | FakeResponse | Exception] | None = None,
    ) -> None:
        self.pages = {
            (method.upper(), url): value for (method, url), value in (pages or {}).items()
        }
        self.calls: list[tuple[str, str]] = []
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        method = method.upper()
        self.calls.append((method, url))
        self.sent.append(dict(kwargs))
        if self.closed:
            raise RuntimeError('FakeHttp is closed')
        try:
            value = self.pages[method, url]
        except KeyError:
            raise LookupError(f'No FakeHttp fixture for {method} {url}') from None
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            return FakeResponse(text=value, url=url)
        return FakeResponse(
            text=value.text,
            status_code=value.status_code,
            url=value.url or url,
            content=value.content,
            headers=value.headers,
        )

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeHttp:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()
