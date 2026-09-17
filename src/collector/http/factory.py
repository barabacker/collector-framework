"""build_http_client — assemble an HttpClient from a parser class."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession

from collector.http.client import HttpClient
from collector.http.hooks import Throttle, log_request, log_response
from collector.http.middleware import Middleware
from collector.http.tls import ca_bundle_with_extra_cert
from collector.settings import Settings
from collector.spider import BaseParser


def build_http_client(parser_cls: type[BaseParser]) -> HttpClient:
    """Assemble an ``HttpClient`` from what ``parser_cls.settings`` declares."""
    settings = parser_cls.settings
    middleware = Middleware()
    middleware.request(log_request)
    if settings.delay or settings.delay_jitter:
        middleware.request(Throttle(settings.delay, settings.delay_jitter))
    for request_hook in settings.request_hooks:
        middleware.request(request_hook)

    middleware.response(log_response)
    for response_hook in settings.response_hooks:
        middleware.response(response_hook)

    session: AsyncSession[Any] = AsyncSession(**session_kwargs(parser_cls, settings))
    return HttpClient(session, middleware, retry=settings.retry)


def session_kwargs(parser_cls: type[BaseParser], settings: Settings) -> dict[str, Any]:
    """Translate settings into ``AsyncSession`` keyword arguments."""
    kwargs: dict[str, Any] = {}
    if settings.impersonate is not None:
        kwargs['impersonate'] = settings.impersonate
    if settings.timeout is not None:
        kwargs['timeout'] = settings.timeout
    if settings.proxy:
        kwargs['proxy'] = settings.proxy
    if settings.headers:
        kwargs['headers'] = dict(settings.headers)

    if settings.extra_ca_cert:
        # Resolved against the file the parser class is defined in, so a site's
        # certificate can live next to the parser that needs it.
        cert_path = Path(inspect.getfile(parser_cls)).parent / settings.extra_ca_cert
        kwargs['verify'] = ca_bundle_with_extra_cert(str(cert_path))
    elif settings.skip_tls_verify:
        kwargs['verify'] = False

    kwargs.update(settings.session_kwargs)
    return kwargs
