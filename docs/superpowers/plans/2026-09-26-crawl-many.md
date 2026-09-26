# crawl_many() Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `crawl_many(crawlers, concurrency=, params=, consume=, log=)` runs many crawlers at once — capped, each through `open_crawl()`, each failure kept to its own `Outcome` — and yields the outcomes as crawlers finish.

**Architecture:** A new module `collector/engine/many.py` built entirely on `open_crawl()`: an async generator that checks `params` for every class up front, starts one task per crawler behind an optional semaphore, yields `Outcome`s via `asyncio.as_completed`, and cancels what is left in its `finally`. Exported from `collector.engine` and the package root.

**Tech Stack:** Python ≥ 3.11, asyncio, pytest (+ pytest-asyncio, `asyncio_mode = "auto"`), ruff (single quotes, line length 100; it also formats Python blocks in Markdown), uv.

**Spec:** `docs/superpowers/specs/2026-09-26-crawl-many-design.md`

**Conventions** (every task):
- Single quotes; `from __future__ import annotations` at the top of every module.
- Test names are sentences. Docstrings and comments explain *why*, at the density of the surrounding files (read `src/collector/engine/runner.py` for tone).
- Run everything through `uv run`. Baseline: `uv run pytest -q` → `261 passed, 15 deselected`.
- Lint gate after every task: `uv run ruff check . && uv run ruff format --check .`; if format complains about a file you touched, `uv run ruff format <file>`.
- Commit messages: imperative, capitalised, no prefix, ending with a `Co-Authored-By:` line for the model writing the commit.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `src/collector/engine/many.py` | create | `Outcome`, `crawl_many()`, the per-crawler task |
| `tests/test_many.py` | create | all behaviour tests |
| `src/collector/engine/__init__.py`, `src/collector/__init__.py` | modify | export `crawl_many`, `Outcome` |
| `tests/test_package.py` | modify | the root's export list grows by two, deliberately |
| `examples/many.py`, `examples/README.md`, `README.md`, `CHANGELOG.md` | create/modify | document it |

---

### Task 1: `crawl_many()` and `Outcome`

**Files:**
- Create: `src/collector/engine/many.py`
- Test: `tests/test_many.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_many.py`:

```python
"""crawl_many: many crawlers at once, capped, each failure kept to itself."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import Crawl, Crawler
from collector.engine.many import Outcome, crawl_many


class _Http(FakeHttp):
    """FakeHttp that can stand where open_crawl() expects a session."""

    async def __aenter__(self) -> _Http:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


def _patch_client(monkeypatch, fail_for: set[type[Crawler]] | None = None) -> list[type[Crawler]]:
    """Replace the client factory; record which crawler classes asked for one."""
    built: list[type[Crawler]] = []

    def factory(crawler_cls: type[Crawler], **kwargs: Any) -> _Http:
        built.append(crawler_cls)
        if fail_for and crawler_cls in fail_for:
            raise OSError('no route to host')
        return _Http()

    monkeypatch.setattr('collector.engine.runner.build_http_client', factory)
    return built


def _crawler(site: str, *, fail: bool = False) -> type[Crawler]:
    """A one-page crawler that logs, then emits one item — or raises."""

    class One(Crawler):
        name = site
        start_urls = [f'https://{site}.test/']

        async def parse(self, response: Any):
            await self.log(f'parsing {site}')
            if fail:
                raise RuntimeError(f'{site} broke')
            yield {'site': site}

    return One


async def drain(crawlers: list[type[Crawler]], **kwargs: Any) -> list[Outcome]:
    return [outcome async for outcome in crawl_many(crawlers, **kwargs)]


# ── running ──────────────────────────────────────────────────────────────────


async def test_outcomes_arrive_as_crawlers_finish(monkeypatch):
    _patch_client(monkeypatch)
    delays = {'slow': 0.05, 'fast': 0.0}

    async def consume(crawl: Crawl) -> None:
        await asyncio.sleep(delays[crawl.crawler.name])
        await crawl.run()

    outcomes = await drain([_crawler('slow'), _crawler('fast')], consume=consume)

    assert [outcome.crawler_cls.name for outcome in outcomes] == ['fast', 'slow']
    assert all(outcome.error is None for outcome in outcomes)
    assert all(outcome.crawl.stats.items == 1 for outcome in outcomes)
    assert all(outcome.elapsed >= 0 for outcome in outcomes)


async def test_no_more_than_concurrency_crawlers_run_at_once(monkeypatch):
    _patch_client(monkeypatch)
    running = peak = 0

    async def consume(crawl: Crawl) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        await crawl.run()
        running -= 1

    outcomes = await drain([_crawler(f's{i}') for i in range(5)], concurrency=2, consume=consume)

    assert len(outcomes) == 5
    assert peak == 2


KEPT: list[Any] = []


class _Keeping(Crawler):
    name = 'keeping'
    start_urls = ['https://keeping.test/']

    async def parse(self, response: Any):
        yield {'n': 1}

    async def process_item(self, item: Any) -> None:
        KEPT.append(item)


async def test_without_a_consumer_items_reach_process_item(monkeypatch):
    _patch_client(monkeypatch)
    KEPT.clear()

    await drain([_Keeping])

    assert KEPT == [{'n': 1}]


async def test_log_lines_carry_the_crawlers_name(monkeypatch):
    _patch_client(monkeypatch)
    lines: list[tuple[str, str]] = []

    async def log(name: str, message: str) -> None:
        lines.append((name, message))

    await drain([_crawler('a'), _crawler('b')], log=log)

    assert sorted(lines) == [('a', 'parsing a'), ('b', 'parsing b')]


async def test_without_a_log_lines_go_to_the_logger_tagged(monkeypatch, caplog):
    _patch_client(monkeypatch)
    caplog.set_level(logging.INFO, logger='collector.engine.many')

    await drain([_crawler('a')])

    assert '[a] parsing a' in caplog.text


async def test_a_concurrency_below_one_is_an_error(monkeypatch):
    _patch_client(monkeypatch)
    with pytest.raises(ValueError, match='concurrency'):
        await drain([_crawler('a')], concurrency=0)


# ── failures ─────────────────────────────────────────────────────────────────


async def test_a_failing_crawler_keeps_its_stats_and_spares_the_others(monkeypatch):
    _patch_client(monkeypatch)

    outcomes = {
        outcome.crawler_cls.name: outcome
        for outcome in await drain([_crawler('bad', fail=True), _crawler('good')])
    }

    bad = outcomes['bad']
    # The original failure, not the CrawlError wrapped around it.
    assert isinstance(bad.error, RuntimeError)
    assert str(bad.error) == 'bad broke'
    assert bad.crawl is not None
    assert bad.crawl.stats.errors == 1
    assert outcomes['good'].error is None


async def test_a_failing_consumer_is_isolated_too(monkeypatch):
    _patch_client(monkeypatch)

    async def consume(crawl: Crawl) -> None:
        if crawl.crawler.name == 'picky':
            raise KeyError('no room')
        await crawl.run()

    outcomes = {
        outcome.crawler_cls.name: outcome
        for outcome in await drain([_crawler('picky'), _crawler('easy')], consume=consume)
    }

    assert isinstance(outcomes['picky'].error, KeyError)
    assert outcomes['picky'].crawl is not None
    assert outcomes['easy'].error is None


async def test_a_crawler_whose_client_cannot_be_built_has_no_crawl(monkeypatch):
    down, up = _crawler('down'), _crawler('up')
    _patch_client(monkeypatch, fail_for={down})

    outcomes = {outcome.crawler_cls.name: outcome for outcome in await drain([down, up])}

    assert isinstance(outcomes['down'].error, OSError)
    assert outcomes['down'].crawl is None
    assert outcomes['up'].error is None


@dataclass(frozen=True)
class _Window:
    since: date | None = None


class _Dated(Crawler):
    name = 'dated'
    start_urls = ['https://dated.test/']
    params = _Window()

    async def parse(self, response: Any):
        yield {}


async def test_bad_params_for_one_crawler_stop_the_run_before_anything_is_built(monkeypatch):
    built = _patch_client(monkeypatch)

    with pytest.raises(ValueError, match=r"_Dated: params\['since'\]"):
        await drain([_crawler('free'), _Dated], params={'since': 'someday'})

    assert built == []


# ── leaving early ────────────────────────────────────────────────────────────


async def test_leaving_early_cancels_the_crawlers_still_running(monkeypatch):
    _patch_client(monkeypatch)
    reasons: dict[str, str] = {}

    class Slow(Crawler):
        name = 'slow'
        start_urls = ['https://slow.test/']

        async def parse(self, response: Any):
            await asyncio.sleep(10)
            yield {}

        async def closed(self, stats: Any) -> None:
            reasons['slow'] = stats.reason

    fast = _crawler('fast')

    async with contextlib.aclosing(crawl_many([Slow, fast])) as outcomes:
        async for outcome in outcomes:
            assert outcome.crawler_cls is fast
            break

    assert reasons == {'slow': 'cancelled'}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_many.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'collector.engine.many'`.

- [ ] **Step 3: Implement**

Create `src/collector/engine/many.py`:

```python
"""Running many crawlers at once: a cap, a name on every log line, and each
crawler's failure kept to itself.

An application crawling several sites needs the same few things around
``open_crawl()`` every time — how many run at once, which crawler a log line
came from, one site failing without stopping the rest, and a result per crawler
as it finishes. What happens to each crawler's items stays the application's:
``consume`` gets the open crawl, and without it the crawler's own
``process_item()`` sees them.

    async for outcome in crawl_many(crawlers, concurrency=16, consume=consume):
        print(outcome.crawler_cls.name, outcome.error or outcome.crawl.stats)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from collector.crawler.crawler import Crawler
from collector.crawler.params import resolve_params
from collector.engine.crawl import Crawl, CrawlError
from collector.engine.runner import open_crawl

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Outcome:
    """How one crawler's run ended."""

    crawler_cls: type[Crawler]
    #: The crawl that ran, with its stats and errors — kept on a failure too, so
    #: a site that fell over after two thousand items still says so. ``None``
    #: only when it failed before a crawl existed (building the HTTP client).
    crawl: Crawl | None
    #: ``None`` on success; otherwise the original failure, not the
    #: ``CrawlError`` that carried it out of ``open_crawl()``.
    error: Exception | None
    #: Seconds from this crawler's start — after waiting for a slot — to its end.
    elapsed: float


async def crawl_many(
    crawlers: Iterable[type[Crawler]],
    *,
    concurrency: int | None = None,
    params: Mapping[str, Any] | None = None,
    consume: Callable[[Crawl], Awaitable[None]] | None = None,
    log: Callable[[str, str], Awaitable[None]] | None = None,
) -> AsyncIterator[Outcome]:
    """Run ``crawlers`` at once and yield an ``Outcome`` as each one finishes.

    At most ``concurrency`` run together (``None``: no cap). The same
    ``params`` go to every crawler, and are checked for all of them before any
    starts: a value one crawler refuses stops the run while nothing has been
    built. ``consume(crawl)`` handles a crawler's items — typically by
    iterating ``crawl.stream()`` — and without it the crawl just runs.
    ``log(name, message)`` receives every crawler's log lines with its name.

    A caller that stops early wraps this in ``contextlib.aclosing()``: closing
    the generator is what cancels the crawlers still running, and Python closes
    an abandoned one only when it gets around to finalising it.
    """
    classes = list(crawlers)
    params = params or {}
    if concurrency is not None and concurrency < 1:
        raise ValueError(f'concurrency must be at least 1, got {concurrency}')
    for crawler_cls in classes:
        try:
            resolve_params(crawler_cls.params, params)
        except ValueError as exc:
            raise ValueError(f'{crawler_cls.__name__}: {exc}') from exc

    gate = asyncio.Semaphore(concurrency) if concurrency is not None else None
    tasks = [
        asyncio.create_task(_run_one(crawler_cls, gate, params, consume, log))
        for crawler_cls in classes
    ]
    try:
        for finished in asyncio.as_completed(tasks):
            yield await finished
    finally:
        # Reached when the caller stops early, too: crawlers left running would
        # outlive the loop that is about to stop listening to them.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _run_one(
    crawler_cls: type[Crawler],
    gate: asyncio.Semaphore | None,
    params: Mapping[str, Any],
    consume: Callable[[Crawl], Awaitable[None]] | None,
    log: Callable[[str, str], Awaitable[None]] | None,
) -> Outcome:
    """One crawler from slot to outcome. Any ``Exception`` ends up in the outcome."""
    async with gate if gate is not None else contextlib.nullcontext():
        started = time.monotonic()
        crawl: Crawl | None = None
        error: Exception | None = None
        try:
            async with open_crawl(
                crawler_cls, params=params, log=_tagged(crawler_cls, log)
            ) as crawl:
                if consume is not None:
                    await consume(crawl)
                else:
                    await crawl.run()
        except CrawlError as exc:
            crawl = exc.crawl
            error = exc.__cause__ if isinstance(exc.__cause__, Exception) else exc
        except Exception as exc:  # noqa: BLE001 — one crawler must not stop the others
            # Raised before open_crawl() had a crawl to wrap it with.
            crawl, error = None, exc
        return Outcome(crawler_cls, crawl, error, time.monotonic() - started)


def _tagged(
    crawler_cls: type[Crawler], log: Callable[[str, str], Awaitable[None]] | None
) -> Callable[[str], Awaitable[None]]:
    """The one-argument log a crawl expects, naming the crawler it came from."""
    name = getattr(crawler_cls, 'name', crawler_cls.__name__)

    async def tagged(message: str) -> None:
        if log is None:
            logger.info('[%s] %s', name, message)
        else:
            await log(name, message)

    return tagged
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_many.py -q`
Expected: all pass. If `test_leaving_early_cancels_the_crawlers_still_running` hangs or fails, the `finally` in `crawl_many` is not reached on `aclose()` — investigate rather than loosening the test.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/engine/many.py tests/test_many.py
git commit -m "Add crawl_many(): run many crawlers at once, each failure kept to itself"
```
(plus the `Co-Authored-By:` line.)

---

### Task 2: Export `crawl_many` and `Outcome`

**Files:**
- Modify: `src/collector/engine/__init__.py`
- Modify: `src/collector/__init__.py`
- Modify: `tests/test_package.py`

- [ ] **Step 1: Update the export test**

In `tests/test_package.py`, in `test_the_root_exports_what_a_crawler_author_writes`, add two names to the expected set, after `'collect',`:

```python
('crawl_many',)
('Outcome',)
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_package.py -q`
Expected: FAIL — the set differs by `crawl_many` and `Outcome`.

- [ ] **Step 3: Export**

In `src/collector/engine/__init__.py`, add after the `runner` import:

```python
from collector.engine.many import Outcome, crawl_many
```

add `'Outcome',` after `'CrawlError',` and `'crawl_many',` after `'crawl',` in `__all__`, and add this sentence to the end of the module docstring's paragraph:

```
``many`` runs several crawlers at once over that same single place.
```

In `src/collector/__init__.py`, change

```python
from collector.engine import Crawl, CrawlError, Stats, collect, crawl, open_crawl, run_crawler
```

to

```python
from collector.engine import (
    Crawl,
    CrawlError,
    Outcome,
    Stats,
    collect,
    crawl,
    crawl_many,
    open_crawl,
    run_crawler,
)
```

and add `'Outcome',` after `'CrawlerContext',` and `'crawl_many',` after `'crawl',` in `__all__` (keep the existing sorted order: capitalised names, then `__version__`, then lower-case).

In `tests/test_many.py`, change `from collector.engine.many import Outcome, crawl_many` and `from collector import Crawl, Crawler` into one line:

```python
from collector import Crawl, Crawler, Outcome, crawl_many
```

- [ ] **Step 4: Run and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green (run `uv run ruff check --fix .` first if isort complains).

- [ ] **Step 5: Commit**

```bash
git add src/collector/engine/__init__.py src/collector/__init__.py tests/test_package.py tests/test_many.py
git commit -m "Export crawl_many and Outcome from the package root"
```
(plus the `Co-Authored-By:` line.)

---

### Task 3: Example and documentation

**Files:**
- Create: `examples/many.py`
- Modify: `examples/README.md`, `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Write the example**

Create `examples/many.py`:

```python
"""Many crawlers at once: a cap, each crawler's items handled on their own, and
an outcome per crawler as it finishes.

``crawl_many()`` opens each crawler as ``open_crawl()`` would and hands it to
``consume``, which decides what to do with its items — here, count them. A
crawler that fails lands in its own outcome, stats and all, and the others
carry on.

    uv run python examples/many.py
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from collector import Crawl, Crawler, Response, Settings, crawl_many


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(delay=0.3, max_requests=2)

    async def parse(self, response: Response) -> Any:
        page = response.selector()
        for quote in page.css('div.quote'):
            yield {'author': quote.css('small.author::text').get()}
        next_page = page.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


class Love(Quotes):
    """Same shape, another start: a second site as far as crawl_many knows."""

    name = 'love'
    start_urls = ['https://quotes.toscrape.com/tag/love/']


async def run() -> None:
    counts: dict[str, int] = {}

    async def consume(crawl: Crawl) -> None:
        name = crawl.crawler.name
        async for _item in crawl.stream():
            counts[name] = counts.get(name, 0) + 1

    async for outcome in crawl_many([Quotes, Love], concurrency=2, consume=consume):
        name = outcome.crawler_cls.name
        status = f'failed: {outcome.error!r}' if outcome.error else f'{counts.get(name, 0)} items'
        print(f'{name:<8} {status} in {outcome.elapsed:.1f}s')
    print(f'total: {sum(counts.values())} items')


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    asyncio.run(run())


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: List it**

In `examples/README.md`: change `Eight runnable scripts` to `Nine runnable scripts`; add this row after the `streaming.py` row:

```markdown
| [`many.py`](many.py) | `crawl_many()`: several crawlers at once, capped, each one's items consumed on their own and an outcome per crawler as it finishes. |
```

and in the "They use the network" paragraph change
`` `quotes.py`, `json_api.py`, `forms.py`, `streaming.py` and `pipeline.py` crawl `` to
`` `quotes.py`, `json_api.py`, `forms.py`, `streaming.py`, `many.py` and `pipeline.py` crawl ``.

Run: `uv run pytest tests/test_examples.py -q` — all pass.

- [ ] **Step 3: Run the example (network)**

Run: `uv run python examples/many.py`
Expected: two lines like `love     … items in …s` / `quotes   20 items in …s` in whichever order they finish, then `total: … items`. If quotes.toscrape.com is unreachable, say so in the report.

- [ ] **Step 4: README and CHANGELOG**

In `README.md`, directly after the "Params" bullet (the one ending "fails the run up front."), add:

```markdown
- **Many crawlers** — `crawl_many(crawlers, concurrency=, params=, consume=,
  log=)` runs a set of crawlers at once, each through `open_crawl()`, at most
  `concurrency` together. `consume(crawl)` handles each crawler's items;
  `log(name, message)` gets every line with the crawler's name. One crawler
  failing lands in its own `Outcome` — with its stats, and the original
  failure rather than the `CrawlError` around it — and the rest carry on;
  outcomes arrive as crawlers finish.
```

In `CHANGELOG.md`, add as the first bullet under `## [Unreleased]` → `### Added`:

```markdown
- `crawl_many(crawlers, concurrency=, params=, consume=, log=)` and `Outcome`
  — run several crawlers at once, each through `open_crawl()`, capped at
  `concurrency`, and yield an `Outcome` (`crawler_cls`, `crawl`, `error`,
  `elapsed`) as each finishes. `consume(crawl)` decides what happens to a
  crawler's items; without it the crawler's own `process_item()` sees them.
  `params` are checked for every crawler before any starts. A failure —
  in the crawl, in `consume`, or building the client — stays in that
  crawler's outcome with its stats and the original exception, and the others
  carry on; closing the generator early cancels whatever is still running.
```

- [ ] **Step 5: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add examples/many.py examples/README.md README.md CHANGELOG.md
git commit -m "Document crawl_many(), with an example running two crawlers at once"
```
(plus the `Co-Authored-By:` line.)
