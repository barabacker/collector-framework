"""Response — wraps the result of ``HttpClient.request()``."""

from __future__ import annotations

import json as jsonlib
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from parsel import Selector

from collector.crawler.request import Request

if TYPE_CHECKING:
    from collector.crawler.crawler import Crawler


class Response:
    """Wraps the result of HttpClient.request().

    curl_cffi's AsyncSession returns an already-materialised response
    (``.text`` / ``.status_code`` are plain attributes, not coroutines), so
    ``text`` / ``status`` are plain attributes here too.
    """

    def __init__(self, raw: Any, request: Request, crawler: Crawler | None = None) -> None:
        self.request = request
        self.metadata = request.metadata
        self.status: int = raw.status_code
        self.text: str = raw.text
        self._raw = raw
        self._selector: Selector | None = None
        #: Only ``follow()`` reads this — everything else here needs no crawl
        #: at all, which is why building one to unit-test ``selector()`` or
        #: ``json()`` is not required.
        self.crawler = crawler

    @property
    def raw(self) -> Any:
        """The underlying client response, for anything this wrapper omits."""
        return self._raw

    @property
    def headers(self) -> Any:
        """The response headers, as the client returned them (lookup is case-insensitive).

        Promoted alongside ``status`` and ``text`` because a crawler reads them
        for the same reasons — a rate limit, a content type, a pagination
        header — and ``raw`` is meant for what this wrapper does *not* cover.
        """
        return self._raw.headers

    def selector(self) -> Selector:
        """A parsel ``Selector`` over the body, built once and reused.

        A page is normally queried more than once — the items, then the link to
        the next page — and parsing the same markup again for the second query
        is pure waste.
        """
        if self._selector is None:
            self._selector = Selector(text=self.text)
        return self._selector

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
        data: dict[str, str] | list[tuple[str, str]] | str | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Request:
        """Build a ``Request`` for a link on this page, resolving it first.

        Forwards everything else to ``crawler.request()`` rather than building
        its own ``Request`` — the two used to duplicate the same field list
        and quietly disagree on what a bare ``callback=None`` means; now there
        is exactly one place that decides.
        """
        return self.crawler.request(
            self.urljoin(href),
            method=method,
            callback=callback,
            metadata=metadata,
            headers=headers,
            data=data,
            params=params,
            json=json,
            cookies=cookies,
        )
