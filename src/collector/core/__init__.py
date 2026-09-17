"""The vocabulary every other package shares: settings, and a text helper.

``Settings`` is read by all three of them — the parser declares it, the HTTP
client is built from it, the crawler takes its pacing and limits out of it — so
it belongs to none of them in particular. ``clean`` is here for the same reason
from the other end: nothing in the framework calls it, and it is a parser
author's tool.
"""

from __future__ import annotations

from collector.core.settings import DEFAULT_RETRY_STATUSES, RetryPolicy, Settings
from collector.core.text import clean

__all__ = [
    'DEFAULT_RETRY_STATUSES',
    'RetryPolicy',
    'Settings',
    'clean',
]
