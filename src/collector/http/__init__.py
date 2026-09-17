"""HTTP layer: a curl_cffi session wrapped in retries and request/response hooks."""

from __future__ import annotations

from collector.http.client import HttpClient
from collector.http.factory import build_http_client
from collector.http.hooks import Throttle, log_request, log_response
from collector.http.middleware import Middleware, RequestHook, ResponseHook
from collector.http.tls import ca_bundle_with_extra_cert

__all__ = [
    'HttpClient',
    'Middleware',
    'RequestHook',
    'ResponseHook',
    'Throttle',
    'build_http_client',
    'ca_bundle_with_extra_cert',
    'log_request',
    'log_response',
]
