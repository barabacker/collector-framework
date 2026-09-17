"""HTTP layer: a curl_cffi session wrapped in retries and request/response hooks."""

from __future__ import annotations

from collector.http.client import HttpClient, build_http_client
from collector.http.hooks import (
    RequestHook,
    ResponseHook,
    Throttle,
    log_request,
    log_response,
)
from collector.http.tls import ca_bundle_with_extra_cert

__all__ = [
    'HttpClient',
    'RequestHook',
    'ResponseHook',
    'Throttle',
    'build_http_client',
    'ca_bundle_with_extra_cert',
    'log_request',
    'log_response',
]
