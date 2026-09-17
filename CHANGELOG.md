# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
