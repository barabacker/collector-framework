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

- The `collector.spider` sub-package is gone: `BaseParser`, `Request` and
  `Response` are now `collector.parser`, `collector.request` and
  `collector.response`, and `ParserContext` lives beside the parser it belongs
  to. Four modules and a package for 240 lines, under a name the documentation
  never used — everything here is a *parser*. Imports from the package root are
  unchanged, which is how every test and the README already did it.

### Removed

- `ParserContext.job_name` and `ParserContext.extra`. Nothing read either one —
  not the framework, not a test, not the README — so they were public API with
  no behaviour behind them. Whatever an application needs to carry belongs on
  its own parser subclass or in `sink`, and a general-purpose bag is not a
  replacement for them.

### Fixed

- `Stats.reason` is `'cancelled'` for a crawl that was stopped rather than
  finished — a consumer breaking out of `stream()`, or a cancel from outside.
  It said `'done'` before, which is what a drained queue says, so a caller
  could not tell a complete crawl from an abandoned one.
- `Response.selector()` builds its `Selector` once and reuses it. It re-parsed
  the markup on every call, so the ordinary shape of a parse — query the items,
  then query the next-page link — parsed the same page twice.
- A hook-driven `retry()` now runs the request middleware again, so `Throttle`
  paces it like any other request. It skipped the request hooks before, which
  meant a response hook solving a challenge put a request on the wire outside
  the `delay` a parser had declared — the site saw twice the rate it was
  promised. The attempt budget is unaffected: a hook's retry still spends none.

### Changed

- The framework is now tested over real sockets. `tests/test_mockhttp.py` drives
  the public API against https://mockhttp.org, so `curl_cffi`, the retry loop's
  sleeps, `Retry-After`, the `Throttle` lock and a `stream()` a consumer walked
  away from are exercised on the wire and not against a stand-in for
  `HttpClient`. The service is stateless, so a scenario like "two 503s then a
  200" cannot be staged there; the file says what that leaves uncovered.
- Those tests are opt-in. They carry a `network` marker that `addopts` excludes,
  so `pytest` still needs no internet; run them with `uv run pytest -m network`.

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
