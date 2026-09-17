"""Text helpers for scraped markup."""

from __future__ import annotations

import re

_WS_RE = re.compile(r'\s+')


def clean(value: str | None) -> str | None:
    """Collapse runs of whitespace to single spaces and strip. ``None`` for empty.

    Scraped cells are full of newlines, tabs and non-breaking spaces; this turns
    them into a single comparable line, and an empty result into ``None`` so a
    missing value never masquerades as an empty string.
    """
    if value is None:
        return None
    cleaned = _WS_RE.sub(' ', value).strip()
    return cleaned or None
