# Error Policy (`max_errors`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Settings.max_errors` (default `0`, overridable per run through `params`) decides how many failed requests a crawl tolerates: up to N it carries on and succeeds; the (N+1)th stops it (`stats.reason == 'max_errors'`) and fails it.

**Architecture:** A forgiving `read_max_errors()` beside `read_max_requests()`; `Crawl._worker` marks the crawl stopped once `stats.errors` passes the limit and then drains the queue without sending, exactly like `max_requests`; `Crawl.run()` raises the first failure only when the limit was passed, and returns the stats otherwise.

**Tech Stack:** Python ≥ 3.11, asyncio, pytest (+ pytest-asyncio, `asyncio_mode = "auto"`), ruff (single quotes, line length 100), uv.

**Spec:** `docs/superpowers/specs/2026-09-26-error-policy-design.md`

**Conventions** (every task):
- Single quotes; `from __future__ import annotations`; test names are sentences; docstrings/comments explain *why*.
- Run everything through `uv run`. Baseline: `uv run pytest -q` → `342 passed, 15 deselected`.
- Lint gate after every task: `uv run ruff check . && uv run ruff format --check .`; if format complains about a file you touched, `uv run ruff format <file>`.
- Commit messages: imperative, capitalised, no prefix, a blank line, then a `Co-Authored-By:` line for the model writing the commit.
- Blocks fenced as `text` are fragments (a line to replace, a parameter to add) — fenced that way so ruff, which formats `python` blocks in Markdown, leaves them alone. Apply them exactly.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `src/collector/settings.py` | modify | `Settings.max_errors` |
| `src/collector/engine/params.py` | modify | `read_max_errors()` |
| `src/collector/crawler/params.py` | modify | `max_errors` in `ENGINE_KEYS` |
| `src/collector/engine/crawl.py` | modify | stop past the limit; fail only past the limit |
| `tests/test_params.py`, `tests/test_crawler_params.py` | modify | the reader; the reserved key |
| `tests/test_crawl.py`, `tests/test_many.py` | modify | the policy in a crawl |
| `examples/errors.py`, `examples/README.md`, `README.md`, `CHANGELOG.md` | modify | documentation |

---

### Task 1: The setting, the per-run reader, the reserved key

**Files:**
- Modify: `src/collector/settings.py`, `src/collector/engine/params.py`, `src/collector/crawler/params.py`
- Test: `tests/test_params.py`, `tests/test_crawler_params.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_params.py`, change the import to

```text
from collector.engine.params import read_concurrency, read_max_errors, read_max_requests, worker_count
```

(let `uv run ruff format` wrap it if it is too long) and append:

```python
@pytest.mark.parametrize(
    ('params', 'expected'),
    [
        ({}, 3),
        ({'max_errors': ''}, 3),
        ({'max_errors': '0'}, 0),
        ({'max_errors': '5'}, 5),
        ({'max_errors': 5}, 5),
        ({'max_errors': '-1'}, 3),
        ({'max_errors': 'lots'}, 3),
    ],
)
def test_read_max_errors_takes_zero_and_falls_back_on_junk(params, expected):
    # Zero is a real value here — "no failures tolerated" — unlike max_requests.
    assert read_max_errors(params, 3) == expected


def test_read_max_errors_keeps_no_limit_when_unset():
    assert read_max_errors({}, None) is None
```

In `tests/test_crawler_params.py`:

1. In `test_an_unknown_key_is_an_error_listing_the_known_ones`, replace

```text
    known = 'at, concurrency, label, max_requests, pages, ratio, since, strict'
```

with

```text
    known = 'at, concurrency, label, max_errors, max_requests, pages, ratio, since, strict'
```

2. In `test_the_engines_own_knobs_are_accepted_alongside_the_fields`, replace

```text
    raw = {'concurrency': '2', 'max_requests': '3', 'pages': '1'}
```

with

```text
    raw = {'concurrency': '2', 'max_requests': '3', 'max_errors': '4', 'pages': '1'}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_params.py tests/test_crawler_params.py -q`
Expected: collection error in `test_params.py` (`cannot import name 'read_max_errors'`), and the two `test_crawler_params.py` tests failing on `max_errors`.

- [ ] **Step 3: Implement**

In `src/collector/settings.py`, in the `# ── policy and hooks ──` group, directly before `retry: RetryPolicy = RetryPolicy()`, add:

```text
    #: Failed requests this crawl tolerates. Up to this many, it carries on and
    #: succeeds with the failures in ``crawl.errors``; one more stops it
    #: (``stats.reason == 'max_errors'``) and fails it. ``0`` — the first
    #: failure — by default; ``None`` never stops or fails on failed requests.
    max_errors: int | None = 0
```

In `src/collector/engine/params.py`, append:

```python
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
```

and in that module's docstring, change `Only the two knobs` to `Only the three knobs` (read the paragraph and keep it true: it now reads `concurrency`, `max_requests` and `max_errors`).

In `src/collector/crawler/params.py`, replace

```text
ENGINE_KEYS = frozenset({'concurrency', 'max_requests'})
```

with

```text
ENGINE_KEYS = frozenset({'concurrency', 'max_requests', 'max_errors'})
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_params.py tests/test_crawler_params.py -q` — all pass.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green (nothing reads the setting yet).

```bash
git add src/collector/settings.py src/collector/engine/params.py src/collector/crawler/params.py tests/test_params.py tests/test_crawler_params.py
git commit -m "Add Settings.max_errors and its per-run override"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 2: The crawl stops past the limit and fails only then

**Files:**
- Modify: `src/collector/engine/crawl.py`
- Test: `tests/test_crawl.py`, `tests/test_many.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crawl.py`:

```python
# ── error policy ─────────────────────────────────────────────────────────────

PAGES = [f'https://example.test/{n}' for n in range(5)]


def _flaky(bad: set[int], **overrides: Any) -> type[Crawler]:
    """Five start pages; the ones numbered in ``bad`` fail in parse()."""

    class _Flaky(Crawler):
        name = 'flaky'
        start_urls = PAGES
        settings = replace(Crawler.settings, **overrides)

        async def parse(self, response: Any):
            if int(response.request.url.rsplit('/', 1)[1]) in bad:
                raise ValueError(f'bad {response.request.url}')
            yield {'url': response.request.url}

    return _Flaky


async def test_by_default_the_first_failure_stops_the_crawl(ctx_factory):
    http = FakeHttp()
    ctx, _ = ctx_factory(http)
    crawl = Crawl(_flaky({1})(ctx))

    with pytest.raises(ValueError, match='bad') as info:
        await crawl.run()

    assert [url for _, url in http.calls] == PAGES[:2]
    assert crawl.stats.reason == 'max_errors'
    assert 'crawl stopped after 1 failed requests (max_errors=0)' in info.value.__notes__


async def test_failures_within_max_errors_are_tolerated(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_flaky({1, 3}, max_errors=2)(ctx))

    stats = await crawl.run()

    assert (stats.requests, stats.errors, stats.items, stats.reason) == (5, 2, 3, 'done')
    assert len(crawl.errors) == 2


async def test_one_failure_past_max_errors_stops_the_crawl(ctx_factory):
    http = FakeHttp()
    ctx, _ = ctx_factory(http)
    crawl = Crawl(_flaky({0, 1, 2, 3, 4}, max_errors=2)(ctx))

    with pytest.raises(ValueError):
        await crawl.run()

    assert len(http.calls) == 3
    assert (crawl.stats.errors, crawl.stats.reason) == (3, 'max_errors')


async def test_without_a_limit_failures_never_stop_or_fail_a_crawl(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())

    stats = await Crawl(_flaky({0, 1, 2, 3, 4}, max_errors=None)(ctx)).run()

    assert (stats.requests, stats.errors, stats.reason) == (5, 5, 'done')


async def test_on_error_sees_the_failure_that_stops_the_crawl(ctx_factory):
    seen: list[str] = []

    class _Watching(_flaky({1})):
        async def on_error(self, request: Any, exc: Exception) -> None:
            seen.append(request.url)

    ctx, _ = ctx_factory(FakeHttp())

    with pytest.raises(ValueError):
        await Crawl(_Watching(ctx)).run()

    assert seen == [PAGES[1]]


async def test_max_errors_can_be_set_per_run(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp(), params={'max_errors': '1'})

    stats = await Crawl(_flaky({1})(ctx)).run()

    assert (stats.errors, stats.reason) == (1, 'done')


async def test_a_streamed_crawl_within_its_tolerance_ends_cleanly(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_flaky({1}, max_errors=1)(ctx))

    items = [item async for item in crawl.stream()]

    assert len(items) == 4
    assert crawl.stats.errors == 1


async def test_a_streamed_crawl_past_its_tolerance_raises_after_its_items(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())
    crawl = Crawl(_flaky({1})(ctx))
    got: list[Any] = []

    with pytest.raises(ValueError):
        async for item in crawl.stream():
            got.append(item)

    assert got == [{'url': PAGES[0]}]


async def test_a_tolerated_crawl_still_raises_a_dataset_that_cannot_flush(ctx_factory):
    ctx, _ = ctx_factory(FakeHttp())

    with pytest.raises(OSError, match='disk full'):
        await Crawl(_flaky({1}, max_errors=1)(ctx), dataset=_BrokenFlush()).run()
```

(`_BrokenFlush`, `replace`, `Any`, `pytest`, `FakeHttp`, `Crawl` and `Crawler` already exist in `tests/test_crawl.py`.)

In `tests/test_many.py`, add `Settings` to the `from collector import ...` line and append:

```python
async def test_a_crawler_within_its_tolerance_has_no_error(monkeypatch):
    _patch_client(monkeypatch)

    class Tolerant(Crawler):
        name = 'tolerant'
        start_urls = ['https://tolerant.test/1', 'https://tolerant.test/2']
        settings = Settings(max_errors=1)

        async def parse(self, response: Any):
            if response.request.url.endswith('/1'):
                raise RuntimeError('bad page')
            yield {}

    [outcome] = await drain([Tolerant])

    assert outcome.error is None
    assert len(outcome.crawl.errors) == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_crawl.py tests/test_many.py -q -k "max_errors or tolerance or tolerated or failure or limit or on_error_sees or flaky"`
Expected: failures — e.g. the default test sends all five pages, `reason` is `'done'`, a tolerated crawl still raises.

- [ ] **Step 3: Implement**

In `src/collector/engine/crawl.py`:

1. Change `from collector.engine.params import read_max_requests, worker_count` to

```text
from collector.engine.params import read_max_errors, read_max_requests, worker_count
```

2. In `Stats`, extend the `reason` comment so it lists the new value — replace

```text
    #: Why the crawl ended: ``'done'`` (the queue drained), ``'max_requests'``
    #: (the ceiling was reached) or ``'cancelled'`` (something stopped it — a
```

with

```text
    #: Why the crawl ended: ``'done'`` (the queue drained), ``'max_requests'``
    #: (the ceiling was reached), ``'max_errors'`` (more requests failed than
    #: it tolerates) or ``'cancelled'`` (something stopped it — a
```

3. In `run()`:

- replace its docstring's first paragraph body

```text
        The first error collected while handling requests is re-raised once all
        workers have finished, so one bad page neither kills a worker nor passes
        silently; see ``errors`` for the rest.
```

with

```text
        A failed request neither kills a worker nor passes silently: it is
        collected in ``errors``. Up to ``max_errors`` of them the crawl carries
        on and returns its stats; one more stops it, and the first failure is
        raised once the workers have finished.
```

- after `limit = read_max_requests(params, crawler.settings.max_requests)` add:

```text
        error_limit = read_max_errors(params, crawler.settings.max_errors)
```

- change the workers line to pass it:

```text
        workers = [
            asyncio.create_task(self._worker(queue, limit, error_limit)) for _ in range(n_workers)
        ]
```

- replace the ending

```text
        if self.errors:
            if flush_error is not None:
                self.errors[0][1].add_note(f'and flushing the dataset failed: {flush_error!r}')
            raise self.errors[0][1]
        if flush_error is not None:
            raise flush_error
        return self.stats
```

with

```text
        if error_limit is not None and self.stats.errors > error_limit:
            first = self.errors[0][1]
            first.add_note(
                f'crawl stopped after {self.stats.errors} failed requests '
                f'(max_errors={error_limit})'
            )
            if flush_error is not None:
                first.add_note(f'and flushing the dataset failed: {flush_error!r}')
            raise first
        if flush_error is not None:
            raise flush_error
        return self.stats
```

4. Replace `_worker` — its signature and the top of the loop, and the end of its `except` — so that it reads:

```python
    async def _worker(
        self, queue: asyncio.Queue[Request], limit: int | None, error_limit: int | None
    ) -> None:
        while True:
            req = await queue.get()
            try:
                if self.stats.reason == 'max_errors':
                    # Stopped for failures: drain without sending, as below, and
                    # before it, so reaching max_requests cannot overwrite why.
                    continue
                if limit is not None and self.stats.requests >= limit:
                    # Reached the cap: drain what is queued without sending it,
                    # so queue.join() still finishes and the crawl ends cleanly.
                    # Marked here rather than on reaching the count, so a crawl
                    # that ends exactly on the limit is still a plain 'done'.
                    self.stats.reason = 'max_requests'
                    continue
                # No await between the check and the increment, so on asyncio's
                # single thread the cap cannot be overshot by racing workers.
                self.stats.requests += 1
                await self._handle(req, queue)
            except Exception as exc:  # noqa: BLE001 — collect, don't kill the worker
                # Note the request on the exception itself, so the traceback
                # raised at the end of the crawl still says which page it was.
                exc.add_note(f'while handling {req.method} {req.url}')
                logger.warning('crawl.error %s %s %r', req.method, req.url, exc)
                self.stats.errors += 1
                self.errors.append((req, exc))
                try:
                    await self.crawler.on_error(req, exc)
                except Exception:  # noqa: BLE001 — a broken hook must not also kill the worker
                    logger.exception('crawl.on_error_failed %s %s', req.method, req.url)
                if error_limit is not None and self.stats.errors > error_limit:
                    # One failure past what this crawl tolerates: stop sending.
                    # Requests already in flight on other workers still finish.
                    self.stats.reason = 'max_errors'
            finally:
                queue.task_done()
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `uv run pytest tests/test_crawl.py tests/test_many.py -q` — the new tests pass.
Run: `uv run pytest -q`.
Expected: the new behaviour is the default, so **existing tests that relied on a crawl carrying on after a failure will fail** (look at `_FailingBoth` users in `tests/test_crawl.py`, and `tests/test_runner.py`, `tests/test_many.py`, `tests/test_mockhttp.py`, `tests/test_examples.py`). For each: if carrying on after a failure is what the test exercises (it asserts two errors, or that later pages were still fetched), give that crawler `max_errors=None` in its settings and list it in your report; if an assertion now reflects the intended new behaviour, report it rather than rewriting it silently. Also run `uv run pytest -q -m network` once (mockhttp.org, already used by the suite) and report the result; note that `test_a_crawl_talks_to_the_real_service` is known to fail on `main` for an unrelated reason (the service truncates the User-Agent).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .` — clean.

```bash
git add src/collector/engine/crawl.py tests/
git commit -m "Stop and fail a crawl only once more requests fail than it tolerates"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 3: Documentation

**Files:**
- Modify: `examples/errors.py`, `examples/README.md`, `README.md`, `CHANGELOG.md`

- [ ] **Step 1: Rewrite `examples/errors.py`**

Replace the whole file with:

```python
"""When pages fail: how many a crawl tolerates, and what it keeps of them.

A request that blows up in ``parse()`` is collected, never lost: the worker
takes the next one, and every failure is kept with the request that caused it.
How many failures a crawl tolerates is ``Settings.max_errors``:

- ``0`` (the default) — the first failure stops the crawl and fails it, without
  sending the rest;
- ``N`` — up to N failures the crawl carries on and succeeds, the failures still
  in ``crawl.errors``; one more stops and fails it;
- ``None`` — failures never stop or fail it.

A crawl that fails still reports everything: it rides out on a ``CrawlError``
as ``exc.crawl`` — the stats, and every failure with its request — and the
original failure is ``exc.__cause__``.

A retryable status is *not* a failure: 429 and the 5xx family are retried inside
the HTTP client, and once the attempt budget is spent the response is handed to
``parse()`` as it is. What to do about a 404 is the crawler's decision, which is
why this one makes it explicitly.

    uv run python examples/errors.py
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from collector import Crawler, CrawlError, Response, RetryPolicy, Settings, run_crawler

BASE = 'https://mockhttp.org'


class Strict(Crawler):
    """The default: the first failure ends it."""

    name = 'strict'
    start_urls = [
        f'{BASE}/get?page=1',
        f'{BASE}/status/404',  # not retryable: the request itself is wrong
        f'{BASE}/status/503',  # retried, then handed over when the budget runs out
        f'{BASE}/get?page=2',
    ]
    settings = Settings(
        delay=0.2,
        # Small numbers so the example does not sit out a real backoff.
        retry=RetryPolicy(attempts=2, multiplier=0.2, min_wait=0.2, max_wait=0.5),
    )

    async def parse(self, response: Response) -> Any:
        if response.status != 200:
            raise ValueError(f'{response.status} for {response.request.url}')
        yield {'url': response.request.url, 'method': response.json()['method']}


class Fragile(Strict):
    """Tolerates one failure; the second stops it."""

    name = 'fragile'
    settings = replace(Strict.settings, max_errors=1)


class Tolerant(Strict):
    """Tolerates both bad pages, and succeeds."""

    name = 'tolerant'
    settings = replace(Strict.settings, max_errors=2)


def report(crawler_cls: type[Crawler]) -> None:
    try:
        crawl = run_crawler(crawler_cls)
        outcome = 'succeeded'
    except CrawlError as exc:
        crawl = exc.crawl
        outcome = f'failed: {exc.__cause__}'

    stats = crawl.stats
    print(f'{crawler_cls.name:<9} {outcome}')
    print(
        f'          {stats.requests} sent, {stats.errors} failed, {stats.items} items, '
        f'reason={stats.reason!r}'
    )
    for request, error in crawl.errors:
        print(f'          {request.url} -> {error}')


def main() -> None:
    report(Strict)
    report(Fragile)
    report(Tolerant)


if __name__ == '__main__':
    main()
```

Run: `uv run pytest tests/test_examples.py -q` — all pass.
Run (network): `uv run python examples/errors.py` — expect `strict` failed with 2 sent and `reason='max_errors'`, `fragile` failed with 3 sent, `tolerant` succeeded with 4 sent, 2 failed, 2 items. Report the actual output; if mockhttp.org is unreachable, say so.

- [ ] **Step 2: `examples/README.md`**

Replace the `errors.py` row's description with:

```text
| [`errors.py`](errors.py) | `max_errors`: the first failure stops a crawl by default; a tolerance lets one with bad pages succeed — and every failure leaves on `crawl.errors`, or on a `CrawlError` as `exc.crawl.errors`. |
```

- [ ] **Step 3: `README.md`**

Read the `Crawl` bullet (around line 49) and the `on_error` bullet (around line 59). Both say the crawl "re-raises the first at the end". Reword each so it is true now — the crawl collects each failure; up to `max_errors` it carries on and returns; past it, it stops and the first failure is raised — keeping the rest of each bullet. Then, directly after the "De-duplication" bullet, add:

```markdown
- **Error policy** — `Settings(max_errors=N)`: up to N failed requests a crawl
  carries on and succeeds, the failures in `crawl.errors` and `stats.errors`;
  one more stops it (`stats.reason == 'max_errors'`, nothing further sent) and
  fails it. `0` by default — the first failure — and `None` for no limit; a run
  can override it with `params={'max_errors': '20'}`.
```

- [ ] **Step 4: `CHANGELOG.md`**

Under `## [Unreleased]` → `### Added`, as the first bullet:

```markdown
- `Settings.max_errors` (default `0`), overridable per run with
  `params['max_errors']`: up to that many failed requests a crawl carries on
  and succeeds with them in `crawl.errors`; one more stops it — nothing further
  is sent, `stats.reason == 'max_errors'` — and fails it. `None` for no limit.
```

Under `### Changed`, as the first bullet:

```markdown
- **Behaviour change:** a crawl no longer runs to the end and then fails
  because some request failed. With the default `max_errors=0` it stops at the
  first failure without sending the rest; a crawler that should survive bad
  pages sets `max_errors`, and `max_errors=None` never stops or fails on them.
```

- [ ] **Step 5: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add examples/errors.py examples/README.md README.md CHANGELOG.md
git commit -m "Document the error policy, with an example of each tolerance"
```
(with the blank line and `Co-Authored-By:` line.)
