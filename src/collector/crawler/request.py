"""Request — describes an HTTP request that ``crawl()`` must perform."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collector.crawler.response import Response

#: Fields a request contributes to ``HttpClient.request`` verbatim, in the order
#: ``http_kwargs()`` reports them. Anything else a site needs is a session-wide
#: concern and belongs in ``Settings.session_kwargs``.
_TRANSPORT_FIELDS = ('headers', 'params', 'data', 'json', 'cookies')


@dataclass(slots=True)
class Request:
    """Describes an HTTP request that crawl() must perform.

    ``callback`` receives the :class:`~collector.crawler.response.Response` and
    yields further requests or items; ``None`` means the crawler's ``parse()``.
    ``metadata`` is carried over to the response untouched.

    ``params`` / ``json`` / ``cookies`` sit alongside ``headers`` / ``data``
    because they vary per request — a page number, a JSON body, a session
    cookie picked up mid-crawl. A field left ``None`` is not sent at all, so
    the transport keeps its own defaults.
    """

    url: str
    method: str = 'GET'
    callback: Callable[[Response], AsyncIterator[Request | Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] | None = None
    #: A list of pairs, not only a dict: a form may send one name twice.
    data: dict[str, str] | list[tuple[str, str]] | str | None = None
    params: dict[str, Any] | list[tuple[str, Any]] | None = None
    json: Any | None = None
    cookies: dict[str, str] | None = None

    def http_kwargs(self) -> dict[str, Any]:
        """The keyword arguments this request hands to ``HttpClient.request``.

        Only fields that were actually set are included: passing ``json=None``
        is not the same as not passing ``json`` at all.
        """
        return {
            name: value for name in _TRANSPORT_FIELDS if (value := getattr(self, name)) is not None
        }
