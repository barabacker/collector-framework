"""Per-crawl item components; storage and its schema belong to the application."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collector.crawler.crawler import Crawler


async def _resolve(value: Any) -> Any:
    """A regular method may return an awaitable without being async def."""
    return await value if inspect.isawaitable(value) else value


class PipelineManager:
    """Construct, open, process and close the components of one run.

    The engine owns cancellation and error precedence. close() attempts every
    ordinary cleanup and returns its errors so none can mask a primary failure.
    """

    def __init__(self, crawler: Crawler) -> None:
        self.crawler = crawler
        self._components: list[Any] = []
        self._opened: list[Any] = []

    async def open(self) -> None:
        for cls in self.crawler.settings.pipelines:
            if not isinstance(cls, type):
                raise TypeError(f'pipeline must be a class, got {cls!r}')
            factory = getattr(cls, 'from_crawler', None)
            component = factory(self.crawler) if factory is not None else cls()
            if inspect.isawaitable(component):
                # Setup belongs in open_crawler(), not an async factory.
                if inspect.iscoroutine(component):
                    component.close()
                raise TypeError(f'pipeline {cls.__name__}.from_crawler must be synchronous')
            for method in ('process_item', 'open_crawler', 'close_crawler'):
                value = getattr(component, method, None)
                if (method == 'process_item' or value is not None) and not callable(value):
                    raise TypeError(f'pipeline {cls.__name__}.{method} must be callable')
            self._components.append(component)
        for component in self._components:
            method = getattr(component, 'open_crawler', None)
            if method is not None:
                await _resolve(method())
            self._opened.append(component)

    async def process(self, item: Any) -> Any:
        for component in self._components:
            item = await _resolve(component.process_item(item))
        return item

    async def close(self) -> list[BaseException]:
        errors: list[BaseException] = []
        while self._opened:
            component = self._opened.pop()
            method = getattr(component, 'close_crawler', None)
            if method is not None:
                try:
                    await _resolve(method())
                except (Exception, asyncio.CancelledError) as exc:
                    # A component's cancellation cannot skip the other owners.
                    errors.append(exc)
        return errors
