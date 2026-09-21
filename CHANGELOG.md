# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Response.headers` — the response headers, alongside `status` and `text`. A
  parser reading a rate limit or a content type had to reach into `raw`, which
  is meant for what this wrapper does not cover.

### Changed

- `build_http_client()` takes a keyword-only `concurrency`: the worker count
  the crawl will really run, which the params can raise above what `Settings`
  declares. `open_crawler()` works it out and passes it, because only that side
  sees both. Left out, the declared value stands, so a direct caller is
  unaffected.

- `BaseParser` is now `Parser`. The `Base` prefix said nothing the `ABC` and
  the abstract `parse()` did not already enforce. Downstream code renames the
  import; nothing else about the class changed. An application that wants a
  gentler move can alias it itself with `BaseParser = Parser`.
- `RequestHook` and `ResponseHook` are typed by the awaitable they return
  rather than declared `async def`. Declaring the call `async def` described an
  implementation — "this method is a coroutine function" — when all the client
  needs is something to await, and it formally excluded callables that return
  an awaitable without being coroutine functions: a hook factory, a
  `functools.partial` around a coroutine, an object with `__await__`. Those
  worked but did not match the protocol. Existing `async def` hooks satisfy the
  new form unchanged, and nothing about this reaches runtime: the protocols are
  annotations only, with no `runtime_checkable` and no `isinstance` behind them.
- `Middleware` is gone. `HttpClient` takes `request_hooks` and `response_hooks`
  as two ordered tuples, keyword-only, and exposes them under the same names.
  A container whose only decision was order has been replaced by the order
  itself; `RequestHook` and `ResponseHook` move to `collector.http.hooks`,
  beside the hooks they describe, and are still exported from `collector.http`.
- **Behaviour change:** `Settings.response_hooks` now run in the order they are
  declared. They ran back to front before — the LIFO of the container they were
  registered with, never something `Settings` promised — so a parser declaring
  two response hooks got them in the opposite order. Request hooks were already
  in declaration order; the two halves now agree. Logging still runs last on the
  way back, so it reports the response actually returned.
- The package root exports what a *parser* author writes: `Parser`,
  `ParserContext`, `Request`, `Response`, `Settings`, `RetryPolicy`,
  `DEFAULT_RETRY_STATUSES`, `Crawler`, `Stats` and the four entry points —
  fourteen names instead of twenty-four. The transport moves to
  `collector.http`, which is what a *hook* author writes: `HttpClient`,
  `RequestHook`, `ResponseHook`, `Throttle`, `build_http_client` and
  `ca_bundle_with_extra_cert` are imported from there now. Which import you
  reach for says which of the two you are doing.
- `http/factory.py` is gone; `build_http_client` lives in `collector.http.client`
  next to what it builds, and is still exported from `collector.http`.
- `examples/` — seven runnable scripts, one per topic: pagination, a JSON API,
  streaming with an early `break`, `process_item()` and a sink, what a failed
  page leaves behind, request and response hooks, and the pacing knobs. Each
  keeps its crawl behind `if __name__ == '__main__'`, and the suite imports all
  of them so an API change breaks them here rather than in front of a reader.
- The modules are laid out in three packages, by what someone reaching for them
  is doing: `collector.spider` (`Parser`, `ParserContext`, `Request`,
  `Response`), `collector.engine` (`Crawler`, `Stats` and the four entry points)
  and `collector.http` (the transport). Each re-exports its own names.
  `collector.settings` stays a module beside them, because all three read it and
  it belongs to none. Imports from the package root are unchanged, which is how
  every test and the README already did it.
- **Logger names moved with them.** Anything configuring logging per module
  wants `collector.engine.crawler`, `collector.engine.runner` and
  `collector.engine.params` where it used to want `collector.crawler`,
  `collector.runner` and `collector.params`. The `collector` root logger still
  catches all of them.
- The framework is now tested over real sockets. `tests/test_mockhttp.py` drives
  the public API against https://mockhttp.org, so `curl_cffi`, the retry loop's
  sleeps, `Retry-After`, the `Throttle` lock and a `stream()` a consumer walked
  away from are exercised on the wire and not against a stand-in for
  `HttpClient`. The service is stateless, so a scenario like "two 503s then a
  200" cannot be staged there; the file says what that leaves uncovered.
- Those tests are opt-in. They carry a `network` marker that `addopts` excludes,
  so `pytest` still needs no internet; run them with `uv run pytest -m network`.

### Removed

- `on_item`, the callback on `Crawler`, `crawl()`, `run_parser()` and
  `open_crawler()`. An item now reaches its consumer two ways instead of three:
  `process_item()` pushes it and `stream()` pulls it. A synchronous caller that
  wants the items keeps them on the parser and reads them back off
  `crawler.parser` — which is what the README already recommends for an
  application's own counters — or calls `collect()`, which now drains
  `stream()` and still runs the class it was given, unsubclassed.
- `read_max_pages()` and `read_flag()`. Neither was ever called by the
  framework. `read_max_pages` was the worse of the two: it promised a
  `max_pages` convention that the engine does not honour, so a parser that
  trusted the README got a knob that quietly did nothing. `read_concurrency`
  and `read_max_requests` stay — the crawler reads them — but as
  `collector.engine.params`, not package-level API.
- `clean()`, the whitespace helper, and the `collector.text` module it lived
  in. Nothing in the framework ever called it and the README never mentioned
  it: a convenience for writing `parse()`, which is an application's own code.
  It is four lines of `re.sub` an application keeps where it uses it.
- `ParserContext.job_name` and `ParserContext.extra`. Nothing read either one —
  not the framework, not a test, not the README — so they were public API with
  no behaviour behind them. Whatever an application needs to carry belongs on
  its own parser subclass or in `sink`, and a general-purpose bag is not a
  replacement for them.

### Fixed

- `concurrency` above ten does something again. The HTTP session's connection
  pool (`curl_cffi`'s `max_clients`) defaults to ten and was never set, so a
  crawl declaring twenty workers got twenty — and ten of them waited on the
  pool rather than on the site. Measured against a local server, twenty
  workers took exactly as long as ten and forty took the same again; the
  session is now sized to the worker count and each is the batch it should be.
  `Settings.session_kwargs` still overrides it for anyone who wants a pool
  narrower than the crawl.
- A crawl declaring no workers no longer hangs. `Settings(concurrency=0)` —
  or a bad `concurrency` param falling back to it — started zero workers and
  then waited on a queue nobody was draining, for ever. The worker count is
  worked out in one place now, `worker_count()`, with a floor of one; it was
  being derived in three, and only two of them had that floor.
- `Stats.reason` is `'cancelled'` for a crawl that was stopped rather than
  finished — a consumer breaking out of `stream()`, or a cancel from outside.
  It said `'done'` before, which is what a drained queue says, so a caller
  could not tell a complete crawl from an abandoned one.
- `Response.selector()` builds its `Selector` once and reuses it. It re-parsed
  the markup on every call, so the ordinary shape of a parse — query the items,
  then query the next-page link — parsed the same page twice.
- A hook-driven `retry()` now runs the request hooks again, so `Throttle`
  paces it like any other request. It skipped the request hooks before, which
  meant a response hook solving a challenge put a request on the wire outside
  the `delay` a parser had declared — the site saw twice the rate it was
  promised. The attempt budget is unaffected: a hook's retry still spends none.

## [0.0.1] — 2026-09-17

First release, extracted from the scraper it grew in.

### Added

- `BaseParser` — declarative: `parse()` is an async generator yielding a
  `Request` to follow or anything else to emit as an item, alongside
  `start_requests()`, `process_item()` and `log()`. It holds no run state of
  its own.
- `Crawler` — the engine, and what running a parser gives back. A
  producer/consumer queue with `concurrency` workers; a failed request is
  collected rather than killing its worker, the first error is re-raised once
  the crawl ends and the rest stay in `errors`. Carries `stats` and the parser
  instance.
- `Crawler.stream()` — items as the crawl produces them, over a channel
  bounded by `concurrency`, so a slow consumer applies backpressure and
  breaking out of the loop stops the crawl.
- `open_crawler()` — an async context manager holding the HTTP session open for
  as long as a `stream()` needs it, and stopping the crawl on the way out;
  `Crawler.aclose()` does that stopping on its own.
- `Settings` — one frozen dataclass per parser for proxy, timeout,
  impersonation, headers, TLS, pacing, limits, retry policy and hooks. A
  subclass narrows its parent's with `dataclasses.replace`.
- `RetryPolicy` — retryable statuses (429, 5xx) alongside transport errors
  under a single attempt budget, with `Retry-After` honoured up to
  `max_retry_after` in both its forms.
- `Settings.max_requests` and `read_max_requests` — a ceiling per crawl, since
  a bug in pagination otherwise runs forever with nothing to stop it.
- `Throttle` request hook, installed by `delay` / `delay_jitter`; it holds the
  gap behind a lock, so it survives `concurrency > 1`.
- HTTP layer: `HttpClient` over `curl_cffi` with retries as an explicit loop,
  `Middleware` with request and response hooks, `build_http_client`,
  `ca_bundle_with_extra_cert`.
- `Request` carrying `headers`, `params`, `data`, `json` and `cookies`;
  `Response.selector()`, `.json()`, `.urljoin()` and `.follow()`.
- Runner: `run_parser` (sync), `crawl` (async) and `collect()`.
- Param readers `read_max_pages`, `read_max_requests`, `read_concurrency` and
  `read_flag`, plus the text helper `clean`.
