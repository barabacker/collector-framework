# Request De-duplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** By default a crawl does not send a request whose key (`METHOD|normalised URL|body hash`) it has already queued; `Request(dont_filter=True)` bypasses that, `Request(unique_key=...)` sets the key, `Settings(dedupe=False)` turns it off, and `Stats.duplicates` counts the drops.

**Architecture:** A pure `request_key()` beside `Request` in `collector/crawler/request.py` computes the key. `Crawl` routes every request it queues — start requests and callback results — through one `_enqueue()` that consults a per-run set of keys. The builders (`Crawler.request`, `Response.follow`, `Response.form_request`) forward the two new `Request` fields.

**Tech Stack:** Python ≥ 3.11, stdlib `hashlib` / `urllib.parse` / `json`, pytest (+ pytest-asyncio, `asyncio_mode = "auto"`), ruff (single quotes, line length 100; it also formats Python blocks in Markdown), uv.

**Spec:** `docs/superpowers/specs/2026-09-26-request-dedupe-design.md`

**Conventions** (every task):
- Single quotes; `from __future__ import annotations` at the top of every module.
- Test names are sentences. Docstrings and comments explain *why*, matching the density of the surrounding file.
- Run everything through `uv run`. Baseline: `uv run pytest -q` → `275 passed, 15 deselected`.
- Lint gate after every task: `uv run ruff check . && uv run ruff format --check .`; if format complains about a file you touched, `uv run ruff format <file>`.
- Commit messages: imperative, capitalised, no prefix, a blank line, then a `Co-Authored-By:` line for the model writing the commit.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `src/collector/crawler/request.py` | modify | `Request.unique_key`, `Request.dont_filter`, `request_key()` |
| `src/collector/crawler/__init__.py` | modify | export `request_key` |
| `src/collector/crawler/crawler.py`, `src/collector/crawler/response.py` | modify | builders accept and forward the two fields |
| `src/collector/settings.py` | modify | `Settings.dedupe` |
| `src/collector/engine/crawl.py` | modify | `Stats.duplicates`, `Crawl._seen`, `Crawl._enqueue()` |
| `tests/test_request_key.py` | create | the key |
| `tests/test_crawler.py`, `tests/test_response.py` | modify | forwarding |
| `tests/test_crawl.py` | modify | the check in a crawl |
| `README.md`, `CHANGELOG.md`, `examples/tuning.py` | modify | documentation |

---

### Task 1: `Request.unique_key`, `Request.dont_filter` and `request_key()`

**Files:**
- Modify: `src/collector/crawler/request.py`
- Modify: `src/collector/crawler/__init__.py`
- Test: `tests/test_request_key.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_request_key.py`:

```python
"""The key a crawl de-duplicates on: which requests count as the same one."""

from __future__ import annotations

from typing import Any

from collector import Request
from collector.crawler import request_key

URL = 'https://example.test/lots'


def key(url: str = URL, **kwargs: Any) -> str:
    return request_key(Request(url=url, **kwargs))


def test_a_request_without_a_body_is_its_method_and_url():
    assert key() == 'GET|https://example.test/lots|'


def test_query_order_does_not_matter():
    assert key(f'{URL}?a=1&b=2') == key(f'{URL}?b=2&a=1')


def test_params_merge_into_the_query():
    assert key(f'{URL}?a=1', params={'b': 2}) == key(f'{URL}?b=2&a=1')
    assert key(f'{URL}?a=1', params=[('b', 2)]) == key(f'{URL}?b=2&a=1')


def test_the_fragment_is_dropped():
    assert key(f'{URL}#top') == key(URL)


def test_the_scheme_and_hosts_case_do_not_matter():
    assert key('HTTPS://Example.TEST/lots') == key(URL)


def test_the_path_is_kept_as_it_is():
    # Either can name a different page; the key does not rewrite what a URL means.
    assert key(f'{URL}/') != key(URL)
    assert key('https://example.test/LOTS') != key(URL)


def test_the_method_matters():
    assert key(method='POST') != key(method='GET')


def test_the_same_form_in_another_order_is_the_same_body():
    assert key(method='POST', data={'a': '1', 'b': '2'}) == key(
        method='POST', data={'b': '2', 'a': '1'}
    )


def test_a_different_body_is_a_different_request():
    # ASP.NET pagination: every page is a POST to the same URL.
    page_2 = key(method='POST', data={'__EVENTTARGET': 'pager$2'})
    page_3 = key(method='POST', data={'__EVENTTARGET': 'pager$3'})
    assert page_2 != page_3


def test_a_list_of_pairs_and_json_are_part_of_the_key():
    assert key(method='POST', data=[('a', '1')]) != key(method='POST', data=[('a', '2')])
    assert key(method='POST', json={'a': 1, 'b': 2}) == key(method='POST', json={'b': 2, 'a': 1})
    assert key(method='POST', json={'a': 1}) != key(method='POST', json={'a': 2})


def test_unique_key_replaces_the_computed_key():
    assert key(unique_key='lot-42') == 'lot-42'


def test_headers_and_cookies_do_not_change_the_key():
    assert key(headers={'X-Trace': '1'}, cookies={'session': 'abc'}) == key()


def test_the_new_fields_do_not_reach_the_transport():
    req = Request(url=URL, unique_key='lot-42', dont_filter=True)
    assert req.http_kwargs() == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_request_key.py -q`
Expected: collection error — `ImportError: cannot import name 'request_key' from 'collector.crawler'`.

- [ ] **Step 3: Implement**

In `src/collector/crawler/request.py`:

1. Replace the imports block

```python
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
```

with

```python
import hashlib
import json as jsonlib
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
```

2. In `class Request`, after the `cookies` field, add:

```python
    #: The key a crawl de-duplicates on, when the computed one is wrong for this
    #: request — see ``request_key()``.
    unique_key: str | None = None
    #: Send this request even if the crawl has already queued one with its key.
    dont_filter: bool = False
```

3. At the end of the module, add:

```python
def request_key(request: Request) -> str:
    """The key a crawl uses to tell whether it has queued this request before.

    ``request.unique_key`` when set; otherwise ``METHOD|url|body``, where the
    URL has its scheme and host lower-cased, its fragment dropped, ``params``
    merged into its query and the query sorted — the path and every parameter
    are kept, since either can name a different page — and ``body`` is a
    sha256 of the request's ``data`` and ``json``, empty without either.

    The method and body are in the key because POSTing to one URL with
    different bodies is how ASP.NET pagination walks its pages: a key of the
    URL alone would stop it at the first.
    """
    if request.unique_key is not None:
        return request.unique_key
    url = _normalise_url(request.url, request.params)
    return f'{request.method.upper()}|{url}|{_body_digest(request.data, request.json)}'


def _normalise_url(url: str, params: Any) -> str:
    parts = urlsplit(url.strip())
    query = parse_qsl(parts.query, keep_blank_values=True)
    if params:
        pairs = params.items() if isinstance(params, Mapping) else params
        query += [(str(name), str(value)) for name, value in pairs]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), '')
    )


def _body_digest(data: Any, json: Any) -> str:
    if data is None and json is None:
        return ''
    digest = hashlib.sha256()
    if data is not None:
        if isinstance(data, str):
            canonical = data
        elif isinstance(data, Mapping):
            # A dict's order is an accident of how it was built, not a
            # difference in what is sent.
            canonical = repr(sorted((str(name), str(value)) for name, value in data.items()))
        else:
            canonical = repr([(str(name), str(value)) for name, value in data])
        digest.update(b'data:' + canonical.encode())
    if json is not None:
        body = jsonlib.dumps(json, sort_keys=True, separators=(',', ':'), default=str)
        digest.update(b'json:' + body.encode())
    return digest.hexdigest()
```

4. In `src/collector/crawler/__init__.py`, change `from collector.crawler.request import Request` to

```python
from collector.crawler.request import Request, request_key
```

and add `'request_key',` at the end of `__all__`.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_request_key.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/crawler/request.py src/collector/crawler/__init__.py tests/test_request_key.py
git commit -m "Give a request a de-duplication key, and a way to set or bypass it"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 2: The request builders forward `unique_key` and `dont_filter`

**Files:**
- Modify: `src/collector/crawler/crawler.py` (`Crawler.request`)
- Modify: `src/collector/crawler/response.py` (`Response.follow`, `Response.form_request`)
- Test: `tests/test_crawler.py`, `tests/test_response.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crawler.py`:

```python
def test_request_carries_its_de_duplication_fields():
    req = _crawler().request(PAGE_2, unique_key='lot-42', dont_filter=True)
    assert (req.unique_key, req.dont_filter) == ('lot-42', True)


def test_request_is_filtered_by_default():
    req = _crawler().request(PAGE_2)
    assert (req.unique_key, req.dont_filter) == (None, False)
```

Append to `tests/test_response.py`:

```python
def test_follow_and_form_request_forward_the_de_duplication_fields():
    crawler = _crawler()
    page = FakeResponse(text='<form method="post"><input name="q" value="x"></form>')
    response = Response(page, Request(url='https://example.test/'), crawler)

    followed = response.follow('/next', unique_key='next', dont_filter=True)
    posted = response.form_request(unique_key='form', dont_filter=True)

    assert (followed.unique_key, followed.dont_filter) == ('next', True)
    assert (posted.unique_key, posted.dont_filter) == ('form', True)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_crawler.py tests/test_response.py -q`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'unique_key'`.

- [ ] **Step 3: Implement**

In `src/collector/crawler/crawler.py`, `Crawler.request()`: add two keyword parameters after `cookies`:

```text
        unique_key: str | None = None,
        dont_filter: bool = False,
```

and pass them to `Request(...)`:

```python
            cookies=cookies,
            unique_key=unique_key,
            dont_filter=dont_filter,
        )
```

Update its docstring line to: `"""Build a ``Request`` defaulting its callback to ``self.parse``."""` → keep it, it is still true.

In `src/collector/crawler/response.py`:

- `follow()`: add the same two parameters after `cookies`, and pass `unique_key=unique_key, dont_filter=dont_filter,` to `self.crawler.request(...)` after `cookies=cookies,`.
- `form_request()`: add the same two parameters after `headers`, and change the final call to

```python
        return self.crawler.request(
            url,
            method=method,
            callback=callback,
            metadata=metadata,
            headers=headers,
            unique_key=unique_key,
            dont_filter=dont_filter,
            **body,
        )
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_crawler.py tests/test_response.py -q` — all pass.

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add src/collector/crawler/crawler.py src/collector/crawler/response.py tests/test_crawler.py tests/test_response.py
git commit -m "Let the request builders set a request's key or bypass de-duplication"
```
(with the blank line and `Co-Authored-By:` line.)

---

### Task 3: De-duplicate in `Crawl`

**Files:**
- Modify: `src/collector/settings.py`
- Modify: `src/collector/engine/crawl.py`
- Test: `tests/test_crawl.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crawl.py`:

```python
# ── de-duplication ───────────────────────────────────────────────────────────


class _Linking(Crawler):
    """Page 1 links to page 2 twice; page 2 links back to page 1."""

    name = 'linking'
    start_urls = [PAGE_1]

    async def parse(self, response: Any):
        if response.request.url == PAGE_1:
            yield self.request(PAGE_2)
            yield self.request(PAGE_2)
        else:
            yield self.request(PAGE_1)


async def test_a_request_already_queued_is_not_sent_again(ctx_factory):
    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Linking(ctx)).run()

    assert sorted(url for _, url in http.calls) == [PAGE_1, PAGE_2]
    assert stats.requests == 2
    assert stats.duplicates == 2


async def test_a_duplicate_start_url_is_dropped_too(ctx_factory):
    class _Twice(_TwoPages):
        start_urls = [PAGE_1, PAGE_1]

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Twice(ctx)).run()

    assert [url for _, url in http.calls].count(PAGE_1) == 1
    assert stats.duplicates == 1


async def test_dont_filter_sends_a_request_again(ctx_factory):
    class _Again(Crawler):
        name = 'again'
        start_urls = [PAGE_1]

        async def parse(self, response: Any):
            if not response.metadata.get('again'):
                yield self.request(PAGE_1, dont_filter=True, metadata={'again': True})

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Again(ctx)).run()

    assert http.calls == [('GET', PAGE_1), ('GET', PAGE_1)]
    assert stats.duplicates == 0


async def test_with_dedupe_off_every_duplicate_is_sent(ctx_factory):
    class _Loose(_Linking):
        settings = replace(_Linking.settings, dedupe=False, max_requests=5)

    ctx, _ = ctx_factory(FakeHttp())

    stats = await Crawl(_Loose(ctx)).run()

    assert stats.requests == 5
    assert stats.duplicates == 0


async def test_posts_to_one_url_with_different_bodies_are_all_sent(ctx_factory):
    class _Pager(Crawler):
        name = 'pager'

        async def start_requests(self):
            for target in ('pager$2', 'pager$3'):
                yield self.request(PAGE_1, method='POST', data={'__EVENTTARGET': target})

        async def parse(self, response: Any):
            yield {}

    http = FakeHttp()
    ctx, _ = ctx_factory(http)

    stats = await Crawl(_Pager(ctx)).run()

    assert http.calls == [('POST', PAGE_1), ('POST', PAGE_1)]
    assert stats.duplicates == 0


async def test_duplicates_do_not_use_up_max_requests(ctx_factory):
    class _Capped(_Linking):
        settings = replace(_Linking.settings, max_requests=2)

    ctx, _ = ctx_factory(FakeHttp())

    stats = await Crawl(_Capped(ctx)).run()

    # Two distinct pages exactly fill the ceiling; the dropped duplicates were
    # never queued, so the crawl drained rather than hitting the limit.
    assert (stats.requests, stats.duplicates, stats.reason) == (2, 2, 'done')
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_crawl.py -q -k "duplicate or dont_filter or dedupe or bodies"`
Expected: FAIL — e.g. `AttributeError: 'Stats' object has no attribute 'duplicates'` and `TypeError: ... unexpected keyword argument 'dedupe'`.

- [ ] **Step 3: Implement**

In `src/collector/settings.py`, directly after the `max_requests` field (and its comment), add:

```python
    #: Drop a request whose key (``request_key()``) this crawl already queued.
    #: A repeated request is almost always a mistake — a lot listed on two
    #: pages, pages linking to each other — so this is on unless a crawler
    #: says otherwise; ``Request(dont_filter=True)`` bypasses it for one request.
    dedupe: bool = True
```

In `src/collector/engine/crawl.py`:

1. Change `from collector.crawler.request import Request` to

```python
from collector.crawler.request import Request, request_key
```

2. In `Stats`, after `items: int = 0`, add:

```python
    #: Requests dropped because this crawl had already queued one with the same
    #: key. Never sent, so not in ``requests``.
    duplicates: int = 0
```

3. In `Crawl`, after the `errors` field, add:

```python
    #: Keys of the requests this run has queued, for ``Settings.dedupe``.
    _seen: set[str] = field(default_factory=set, init=False, repr=False)
```

4. In `run()`, replace

```python
        async for req in crawler.start_requests():
            queue.put_nowait(req)
```

with

```python
        async for req in crawler.start_requests():
            self._enqueue(req, queue)
```

5. In `_handle()`, replace

```python
            if isinstance(result, Request):
                queue.put_nowait(result)
```

with

```python
            if isinstance(result, Request):
                self._enqueue(result, queue)
```

6. Add this method to `Crawl`, directly before `_worker`:

```python
    def _enqueue(self, req: Request, queue: asyncio.Queue[Request]) -> None:
        """Queue a request unless this crawl has already queued one with its key.

        The one door into the queue, for start requests and callbacks alike. A
        request let through with ``dont_filter`` still records its key, so a
        plain request for the same page after it is a duplicate.
        """
        if self.crawler.settings.dedupe:
            key = request_key(req)
            if key in self._seen and not req.dont_filter:
                self.stats.duplicates += 1
                logger.debug('crawl.duplicate %s %s', req.method, req.url)
                return
            self._seen.add(key)
        queue.put_nowait(req)
```

- [ ] **Step 4: Run the new tests, then the whole suite**

Run: `uv run pytest tests/test_crawl.py -q` — all pass.
Run: `uv run pytest -q`.
Expected: all pass. De-duplication is now on by default, so **an existing test that deliberately queues the same request twice will fail**. For each such failure, read the test: if repeating the request is what it exercises, give that crawler `settings = replace(<its parent>.settings, dedupe=False)` (or `dont_filter=True` on the request) and say so in the report; if the repeat was incidental, report it rather than changing the assertion.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .` — clean.

```bash
git add src/collector/settings.py src/collector/engine/crawl.py tests/test_crawl.py
git commit -m "Drop a request the crawl has already queued, unless told not to"
```
(add any other test file you had to adjust; blank line and `Co-Authored-By:` line.)

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `examples/tuning.py`

- [ ] **Step 1: README — the by-design list**

In `README.md`, in "## What you do not get, by design", change

```markdown
No item schema, no storage, no scheduler, no request de-duplication, no
robots.txt, and no registry
```

to

```markdown
No item schema, no storage, no scheduler, no robots.txt, and no registry
```

(re-wrap the rest of that paragraph if the line lengths now look ragged; keep the wording.)

- [ ] **Step 2: README — a bullet**

In `README.md`, directly after the "Many crawlers" bullet, add:

```markdown
- **De-duplication** — a crawl does not send a request it has already queued.
  Two requests are the same when their method, URL (scheme and host
  lower-cased, fragment dropped, query sorted, `params` merged in) and body
  match — so POSTing one URL with different bodies, as ASP.NET pagination
  does, is several requests. `Request(dont_filter=True)` sends one anyway,
  `Request(unique_key=...)` sets the key, `Settings(dedupe=False)` turns it off,
  and `stats.duplicates` counts what was dropped. Keys live for one crawl.
```

- [ ] **Step 3: `examples/tuning.py`**

Replace the comment on the `max_requests=7,` line:

```text
        max_requests=7,  # those pages link back to each other; nothing de-duplicates
```

with

```text
        max_requests=7,  # a ceiling still: de-duplication stops loops, not a site that never ends
```

Run: `uv run pytest tests/test_examples.py -q` — all pass.

- [ ] **Step 4: CHANGELOG**

In `CHANGELOG.md`, under `## [Unreleased]` → `### Added`, add as the first bullet:

```markdown
- Request de-duplication. `request_key(request)` (in `collector.crawler`) is
  `METHOD|url|sha256(body)`, with the URL's scheme and host lower-cased, its
  fragment dropped, `params` merged into its query and the query sorted;
  `Request.unique_key` replaces it and `Request.dont_filter` bypasses the
  check for one request — `Crawler.request()`, `Response.follow()` and
  `Response.form_request()` take both. `Settings.dedupe` (on by default) and
  `Stats.duplicates`. Unlike Crawlee's default, the method and body are part
  of the key, so ASP.NET pagination — POSTs to one URL — is not collapsed.
```

and under `### Changed`, as the first bullet:

```markdown
- **Behaviour change:** a crawl no longer sends a request it has already
  queued in the same run; the repeat is dropped and counted in
  `stats.duplicates`. `Settings(dedupe=False)` restores the old behaviour for a
  crawler, `Request(dont_filter=True)` for one request.
```

- [ ] **Step 5: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

```bash
git add README.md CHANGELOG.md examples/tuning.py
git commit -m "Document request de-duplication"
```
(with the blank line and `Co-Authored-By:` line.)
