# Error policy: `max_errors`

## Problem

A request that fails is collected in `crawl.errors`, the crawl carries on, and
at the end `Crawl.run()` re-raises the first failure — through the entry points
as a `CrawlError`, in `crawl_many()` as `outcome.error`. Two things follow.

- One bad page fails the whole crawl. `geo-info/trading_platform` walks two
  thousand lot pages per site; a single lot page that returns garbage marks the
  site as failed, although 1999 lots were collected.
- A site that is down is walked to the end anyway. Every request fails, each
  after its full retry budget, and the crawl only reports the outage when it
  has tried every page.

Both are one threshold seen from two sides: how many failed requests a crawl
tolerates. Scrapy has the stopping half (`CLOSESPIDER_ERRORCOUNT`); here one
setting decides both whether a crawl stops early and whether it failed.

## Goals

- One setting: up to N failed requests are tolerated; the (N+1)th stops the
  crawl and fails it.
- A crawl that stays within its tolerance succeeds, with its failures still
  visible in `crawl.errors` and `stats.errors`.
- Overridable per run, like `max_requests`.

## Non-goals

- A rate (`max_error_rate`): noisy at the start of a crawl, and it needs a
  minimum sample to mean anything.
- Classifying failures (transport vs parse vs status) or a separate threshold
  per kind.
- Changing retries: a request counts as failed once, after the HTTP client has
  given up on it.

## Design

### The setting

`Settings.max_errors: int | None = 0`, in the policy group:

- `N` — tolerate up to `N` failed requests; the `N + 1`-th stops the crawl
  and fails it;
- `0` (the default) — the first failure stops and fails the crawl;
- `None` — no limit: failures never stop or fail a crawl.

### Per run

`engine/params.py` gains `read_max_errors(params, default)`, read the way
`read_max_requests` is: unset or empty keeps the default; unparsable or
negative is logged (`params.bad_max_errors`) and keeps the default; `0` is a
valid value. `max_errors` joins `ENGINE_KEYS` in `crawler/params.py`, so a
crawler with declared `params` accepts it and cannot declare a field of that
name.

### Stopping

In `Crawl._worker`, as soon as a failure is recorded — before `on_error` runs, so a hook that awaits cannot let other workers send more meanwhile: if
the limit is not `None` and `stats.errors > limit`, the crawl is marked
stopped with `stats.reason = 'max_errors'`. From then on the workers drain the
queue without sending — exactly as they do on reaching `max_requests` — so
`queue.join()` returns, the session closes normally, the dataset is flushed
and `closed()` runs.

Requests already in flight on other workers finish rather than being
cancelled; with `concurrency > 1` a crawl can therefore end with a few more
than `N + 1` failures, all of them in `crawl.errors`. Once a crawl is stopped
for errors, reaching `max_requests` does not overwrite `reason`.

### Failing

A crawl failed when `max_errors` is not `None` and `stats.errors >
max_errors`. Then `run()` raises the first collected failure with a note —
`crawl stopped after {errors} failed requests (max_errors={limit})` — and the
entry points wrap it in `CrawlError` as today; `stream()` raises it after the
last item, as today.

Otherwise `run()` returns the stats, with `reason` as it would have been
(`'done'`, `'max_requests'`) and the tolerated failures in `crawl.errors` and
`stats.errors`. In `crawl_many()` such a crawler's `outcome.error` is `None`.

A dataset that fails to flush at the end is raised when the crawl otherwise
succeeded — tolerated failures included — and noted on the first failure when
the crawl failed, as today.

### Behaviour change

Today a crawl with any failed request runs to the end and then fails. With the
default `max_errors=0` it fails at the first failure instead, without sending
the rest. A crawler that should survive bad pages sets `max_errors`; one that
wants to be told about every failure but never stopped sets `None` and reads
`stats.errors`.

## Testing

`tests/test_crawl.py`:

- default: after the first failure the remaining start URLs are not sent,
  `stats.reason == 'max_errors'`, and `run()` raises the failure with the note;
- `max_errors=2` with two failures among five pages: `run()` returns,
  `stats.errors == 2`, `reason == 'done'`;
- `max_errors=2` with failures on every page: stops after the third;
- `max_errors=None`: every page is tried and the crawl does not fail;
- `on_error` is called for every failure, including the one that stops the
  crawl;
- `stream()` behaves the same (a tolerated crawl ends cleanly; a stopped one
  raises after the last item);
- a tolerated crawl whose dataset fails to flush raises the flush error.

`tests/test_params.py`: `read_max_errors` — unset, `'0'`, `'3'`, negative,
junk.

`tests/test_crawler_params.py`: `max_errors` is accepted beside declared
fields.

`tests/test_many.py`: a crawler within its tolerance has `outcome.error is
None` and its failures on `outcome.crawl.errors`.

Existing tests that rely on a crawl carrying on after a failure get
`max_errors=None` where carrying on is what they test, and are reported.

## Documentation

- `examples/errors.py` shows the default stopping at the first failure, a
  tolerance that lets a crawl with a bad page succeed, and a crawl stopped
  once its tolerance is exceeded.
- README: the `Crawl` and `on_error` bullets stop saying a crawl re-raises the
  first failure at the end regardless; a line on `max_errors`.
- CHANGELOG: Added (`Settings.max_errors`, `params['max_errors']`,
  `reason == 'max_errors'`) and the behaviour change.
