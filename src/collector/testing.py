"""Offline fixtures for crawler tests; no test runner dependency."""

from __future__ import annotations

from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from typing import Any, TypeVar

from curl_cffi.requests.headers import Headers

from collector.crawler import Crawler, CrawlerContext, Request, Response

C = TypeVar('C', bound=Crawler)


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


def make_crawler(
    crawler_cls: type[C],
    *,
    params: Mapping[str, Any] | None = None,
    sink: Any = None,
    http: Any = None,
    log: Callable[[str], Awaitable[None]] | None = None,
) -> C:
    """Construct a real crawler without starting lifecycle hooks or pipelines.

    Supplied resources remain caller-owned. The default HTTP client rejects
    every request until fixtures are explicitly registered.
    """
    return crawler_cls(
        CrawlerContext(
            http=FakeHttp() if http is None else http,
            params={} if params is None else params,
            sink=sink,
            log=log,
        )
    )


def make_response(
    text: str,
    *,
    request: Request,
    crawler: Crawler | None = None,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Wrap text in a real Response; supply crawler for follow/form_request."""
    return Response(
        FakeResponse(text=text, status_code=status, url=request.url, headers=headers),
        request,
        crawler,
    )


async def collect_callback(
    results: AsyncIterable[Any],
) -> tuple[list[Request], list[Any]]:
    """Partition callback output once, without executing requests or pipelines.

    None is an item. Order within each group is preserved; errors and cancellation
    propagate unchanged.
    """
    requests: list[Request] = []
    items: list[Any] = []
    async for result in results:
        if isinstance(result, Request):
            requests.append(result)
        else:
            items.append(result)
    return requests, items
