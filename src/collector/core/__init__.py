"""The vocabulary every other package shares.

``Settings`` is read by all three of them — the parser declares it, the HTTP
client is built from it, the crawler takes its pacing and limits out of it — so
it belongs to none of them in particular.
"""

from __future__ import annotations

from collector.core.settings import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings

__all__ = [
    'DEFAULT_RETRY_STATUSES',
    'RetryPolicy',
    'Settings',
]
