"""ParserContext — parser execution context."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collector.http.client import HttpClient


@dataclass(slots=True)
class ParserContext:
    """What a parser needs to run: HTTP client, params, optional sink and log.

    ``sink`` is deliberately untyped: this framework has no storage contract of
    its own. An application defines what it stores and how, and reads the sink
    back in its own ``process_item()`` override.
    """

    http: HttpClient
    params: dict[str, str] = field(default_factory=dict)
    sink: Any | None = None
    log: Callable[[str], Awaitable[None]] | None = None
    job_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
