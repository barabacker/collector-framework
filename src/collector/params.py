"""Reading the crawler's own knobs out of the free-form ``params`` dict.

Params arrive as strings (a CLI flag, a job payload, a form field), so each
reader is forgiving: unset or unparsable falls back to the default and is
logged rather than raised — a bad knob should not kill a crawl.

Only the two knobs :class:`~collector.crawler.Crawler` actually honours live
here. A parser reading its own params reads its own dict; a helper in this
package would only promise a name the engine does not know.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def read_concurrency(params: dict[str, str], default: int) -> int:
    """Read ``concurrency`` (number of request workers) from the params.

    Falls back to ``default`` (the parser's ClassVar) when unset or invalid; a
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


def read_max_requests(params: dict[str, str], default: int | None) -> int | None:
    """Read ``max_requests`` from the params. ``None`` means no ceiling.

    Falls back to ``default`` (the parser's ``Settings``) when unset or
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
