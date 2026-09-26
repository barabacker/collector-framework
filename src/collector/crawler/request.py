"""Request — describes an HTTP request that ``crawl()`` must perform."""

from __future__ import annotations

import hashlib
import json as jsonlib
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    #: The key a crawl de-duplicates on, when the computed one is wrong for this
    #: request — see ``request_key()``.
    unique_key: str | None = None
    #: Send this request even if the crawl has already queued one with its key.
    dont_filter: bool = False

    def http_kwargs(self) -> dict[str, Any]:
        """The keyword arguments this request hands to ``HttpClient.request``.

        Only fields that were actually set are included: passing ``json=None``
        is not the same as not passing ``json`` at all.
        """
        return {
            name: value for name in _TRANSPORT_FIELDS if (value := getattr(self, name)) is not None
        }


def request_key(request: Request) -> str:
    """The key a crawl uses to tell whether it has queued this request before.

    ``request.unique_key`` when set; otherwise ``METHOD|url|body``, where the
    URL has its scheme and host lower-cased, its fragment dropped, ``params``
    merged into its query and the query sorted — the path and every parameter
    are kept, since either can name a different page — and ``body`` is a
    sha256 of the request's ``data`` and ``json``, empty without either.

    The method and body are in the key because POSTing to one URL with
    different bodies is how ASP.NET pagination walks its pages: a key of the
    URL alone would stop it at the first.
    """
    if request.unique_key is not None:
        return request.unique_key
    url = _normalise_url(request.url, request.params)
    return f'{request.method.upper()}|{url}|{_body_digest(request.data, request.json)}'


def _normalise_url(url: str, params: Any) -> str:
    parts = urlsplit(url.strip())
    # surrogateescape, not the default 'replace': a query escaped in cp1251 or
    # latin-1 is not UTF-8, and decoding it lossily would give two different
    # searches one key — the second silently dropped as a duplicate.
    query = parse_qsl(parts.query, keep_blank_values=True, errors='surrogateescape')
    if params:
        pairs = params.items() if isinstance(params, Mapping) else params
        for name, value in pairs:
            # Sent with doseq, as curl does: a list is the name repeated.
            values = value if isinstance(value, list | tuple) else [value]
            query += [(str(name), str(item)) for item in values]
    # Only the host is case-insensitive; a user name or password is not.
    host = (parts.hostname or '') + (f':{parts.port}' if parts.port is not None else '')
    userinfo, at, _ = parts.netloc.rpartition('@')
    netloc = f'{userinfo}{at}{host}'
    return urlunsplit(
        (
            parts.scheme.lower(),
            netloc,
            parts.path,
            urlencode(sorted(query), errors='surrogateescape'),
            '',
        )
    )


def _body_digest(data: Any, json: Any) -> str:
    if data is None and json is None:
        return ''
    digest = hashlib.sha256()
    if data is not None:
        if isinstance(data, bytes | bytearray):
            # Outside the annotation, but curl sends it, so the key must not choke on it.
            raw = bytes(data)
        elif isinstance(data, str):
            raw = data.encode()
        elif isinstance(data, Mapping):
            # A dict's order is an accident of how it was built, not a
            # difference in what is sent.
            raw = repr(sorted((str(name), str(value)) for name, value in data.items())).encode()
        else:
            raw = repr([(str(name), str(value)) for name, value in data]).encode()
        digest.update(b'data:' + raw)
    if json is not None:
        body = jsonlib.dumps(json, sort_keys=True, separators=(',', ':'), default=str)
        digest.update(b'json:' + body.encode())
    return digest.hexdigest()
