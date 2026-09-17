"""Settings — everything a parser declares about how it talks to a site.

One frozen dataclass instead of a handful of loose class attributes: a parser
sets ``settings = Settings(...)``, and a subclass narrows its parent's with
``dataclasses.replace``. Frozen because the crawl reads it concurrently and a
setting that changes mid-run is a bug, not a feature.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collector.http.middleware import RequestHook, ResponseHook

#: Statuses worth another attempt: rate limiting and the transient 5xx family.
#: A 4xx other than 429 means the request itself is wrong — retrying it is noise.
DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """When to try again, and how long to wait before doing so.

    One policy covers both failure modes: a transport error (the connection
    never produced a response) and a response whose status is in ``statuses``.
    """

    attempts: int = 4
    #: Backoff is ``multiplier * 2 ** (attempt - 1)``, clamped to [min, max] wait.
    multiplier: float = 1.0
    min_wait: float = 1.0
    max_wait: float = 60.0
    statuses: frozenset[int] = DEFAULT_RETRY_STATUSES
    #: Honour a ``Retry-After`` header when the server sends one.
    respect_retry_after: bool = True
    #: Cap on what a server may ask us to wait; longer means give up instead.
    max_retry_after: float = 60.0

    def backoff(self, attempt: int) -> float:
        """Seconds to wait before ``attempt`` + 1 (1-based attempt number)."""
        return min(max(self.multiplier * 2 ** (attempt - 1), self.min_wait), self.max_wait)


@dataclass(frozen=True, slots=True)
class Settings:
    """How a parser's HTTP client is built and how hard it leans on a site."""

    # ── transport ───────────────────────────────────────────────────────────
    #: curl_cffi browser fingerprint; None sends curl's own.
    impersonate: str | None = 'chrome'
    timeout: float | None = 30.0
    proxy: str | None = None
    headers: Mapping[str, str] | None = None
    #: PEM with an extra CA/intermediate certificate, resolved relative to the
    #: file the parser class is defined in.
    extra_ca_cert: str | None = None
    #: Disable TLS verification outright. Only for a certificate that is broken
    #: on the site's side and that no CA bundle can fix.
    skip_tls_verify: bool = False
    #: Escape hatch: passed straight to ``AsyncSession``, wins over the above.
    session_kwargs: Mapping[str, Any] = field(default_factory=dict)

    # ── pacing ──────────────────────────────────────────────────────────────
    #: Request workers running in parallel within one crawl.
    concurrency: int = 1
    #: Minimum seconds between the start of two requests (0 = as fast as it goes).
    delay: float = 0.0
    #: Random extra delay in ``[0, jitter)``, so requests are not metronomic.
    delay_jitter: float = 0.0
    #: Stop after this many requests; None means no ceiling. A safety valve —
    #: a bug in pagination otherwise crawls forever with nothing to stop it.
    max_requests: int | None = None

    # ── policy and hooks ────────────────────────────────────────────────────
    retry: RetryPolicy = RetryPolicy()
    request_hooks: tuple[RequestHook, ...] = ()
    response_hooks: tuple[ResponseHook, ...] = ()
