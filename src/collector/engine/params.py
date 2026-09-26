"""Reading the crawl's own knobs out of the free-form ``params`` dict.

Params arrive as strings (a CLI flag, a job payload, a form field), so each
reader is forgiving: unset or unparsable falls back to the default and is
logged rather than raised — a bad knob should not kill a crawl.

Only the three knobs :class:`~collector.engine.crawl.Crawl` actually honours
live here: ``concurrency``, ``max_requests`` and ``max_errors``. A crawler's
own knobs are declared on ``Crawler.params`` and converted by
:mod:`collector.crawler.params`, strictly; a helper in this package would only
promise a name the engine does not know.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def read_concurrency(params: Mapping[str, Any], default: int) -> int:
    """Read ``concurrency`` (number of request workers) from the params.

    Falls back to ``default`` (the crawler's ClassVar) when unset or invalid; a
    non-positive value is treated as invalid.
    """
    raw = params.get('concurrency')
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        logger.warning('params.bad_concurrency value=%s', raw)
        return default
    return value if value > 0 else default


def worker_count(params: Mapping[str, Any], default: int) -> int:
    """How many workers a crawl will actually run.

    The one answer to that question. It was being worked out in three places —
    the crawl, its stream buffer and the HTTP session's pool — and they did not
    agree: a crawl declaring no workers at all got zero of them and then waited
    on a queue nobody was draining. A floor of one belongs to the question, not
    to whichever caller remembered it.
    """
    return max(read_concurrency(params, default), 1)


def read_max_requests(params: Mapping[str, Any], default: int | None) -> int | None:
    """Read ``max_requests`` from the params. ``None`` means no ceiling.

    Falls back to ``default`` (the crawler's ``Settings``) when unset or
    invalid; a non-positive value is treated as invalid.
    """
    raw = params.get('max_requests')
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        logger.warning('params.bad_max_requests value=%s', raw)
        return default
    return value if value > 0 else default


def read_max_errors(params: Mapping[str, Any], default: int | None) -> int | None:
    """Read ``max_errors`` from the params. ``None`` means no limit.

    Falls back to ``default`` (the crawler's ``Settings``) when unset or
    invalid. Zero is valid — no failure tolerated — and only a negative number
    is treated as invalid.
    """
    raw = params.get('max_errors')
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        logger.warning('params.bad_max_errors value=%s', raw)
        return default
    if value < 0:
        logger.warning('params.bad_max_errors value=%s', raw)
        return default
    return value
