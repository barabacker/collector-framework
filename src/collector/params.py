"""Reading crawl knobs out of the free-form ``params`` dict.

Params arrive as strings (a CLI flag, a job payload, a form field), so each
reader is forgiving: unset or unparsable falls back to the default and is
logged rather than raised — a bad knob should not kill a crawl.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def read_max_pages(params: dict[str, str]) -> int | None:
    """Read ``max_pages`` from the params. ``None`` means no limit."""
    raw = params.get('max_pages')
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except (ValueError, TypeError):
        logger.warning('params.bad_max_pages value=%s', raw)
        return None
    return value if value > 0 else None


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


def read_flag(params: dict[str, str], name: str, default: bool) -> bool:
    """Read a boolean param. ``0/false/no/off`` are false, anything else true."""
    raw = params.get(name)
    if raw is None or raw == '':
        return default
    return raw.strip().lower() not in ('0', 'false', 'no', 'off')
