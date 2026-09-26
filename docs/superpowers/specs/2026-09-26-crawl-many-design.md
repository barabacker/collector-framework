# Running many crawlers at once: `crawl_many()`

## Problem

The framework runs one crawler at a time. An application with many sites —
`geo-info/trading_platform` crawls sixteen — writes the rest itself, and its
`run_all` is about 150 lines of it:

- a semaphore capping how many crawlers run at once;
- a `log` per crawler that tags each line with the crawler's name, because
  sixteen crawls writing to one stream are unreadable otherwise;
- its own consumption of each crawler's items (a store and counters per site);
- a `try`/`except` per crawler so one site failing does not stop the others;
- results as each crawler finishes, for a progress line, and all of them at the
  end, for a summary.

Only the third is specific to that application. The rest is the same for any
project that crawls several sites, and one detail is easy to get wrong: its
`except` records the failure but not the stats, so a site that fails after
two thousand items reports nothing — although `CrawlError` already carries the
crawl, stats included, on `.crawl`.

## Goals

- One call that runs a set of crawlers concurrently, capped, each through
  `open_crawl()` as if it ran alone.
- The application decides what happens to each crawler's items.
- One crawler failing is recorded with its stats and does not affect the
  others.
- Results arrive as each crawler finishes.

## Non-goals

- Discovering crawlers (a registry, a package scan): the caller passes classes.
- Different `params` per crawler. A set that needs that runs `crawl_many()`
  more than once.
- A synchronous wrapper. The caller already runs an event loop or calls
  `asyncio.run()` itself.
- Formatting: timestamps on log lines, progress lines, summary tables.
- Changing when a single crawl counts as failed (a crawl with any failed
  request still raises at the end, as `Crawl.run()` does today).

## Design

### API

```python
async def crawl_many(
    crawlers: Iterable[type[Crawler]],
    *,
    concurrency: int | None = None,
    params: Mapping[str, Any] | None = None,
    consume: Callable[[Crawl], Awaitable[None]] | None = None,
    log: Callable[[str, str], Awaitable[None]] | None = None,
) -> AsyncIterator[Outcome]: ...


@dataclass(slots=True)
class Outcome:
    crawler_cls: type[Crawler]
    #: The crawl that ran, with its stats and errors; None if it failed
    #: before a Crawl existed (building the HTTP client).
    crawl: Crawl | None
    #: None on success; otherwise the original failure, not a CrawlError.
    error: Exception | None
    #: Seconds from this crawler's start to its end.
    elapsed: float
```

Used as:

```python
async def consume(crawl: Crawl) -> None:
    async for item in crawl.stream():
        ...  # anywhere: a file, a queue, a database


async for outcome in crawl_many(crawlers, concurrency=16, consume=consume):
    print(outcome.crawler_cls.name, outcome.error or outcome.crawl.stats)
```

`crawl_many` and `Outcome` are exported from `collector.engine` and from the
package root, beside the other entry points.

### Before anything starts

- `concurrency` below 1 raises `ValueError`. `None` means no cap.
- The same `params` go to every crawler. Each crawler class's parameters are
  resolved with `resolve_params(cls.params, params)` before any crawler starts;
  the first one that fails raises `ValueError` naming the class —
  `Tiny: params['since']: expected date, got 'x'` — chained from the original,
  and nothing has run.

### Running

Each crawler runs in its own task:

1. wait for a slot, if `concurrency` is set;
2. `async with open_crawl(cls, params=params, log=tagged)` — the session,
   hooks, `opened()` and `closed()` behave as in a single run;
3. `await consume(crawl)` if given, otherwise `await crawl.run()`, where items
   reach the crawler's own `process_item()`;
4. build its `Outcome`.

`tagged` is a one-argument log bound to the crawler's `name`, calling
`log(name, message)`. Without `log`, lines go to the `collector.engine.many`
logger at INFO as `[name] message`, as `run_crawler()` does for one crawler.

### Results

`crawl_many` is an async generator yielding each `Outcome` as its crawler
finishes, in completion order.

### Failures

Any `Exception` inside a crawler's task — from the crawl, from `consume`, from
building the client, from `opened()` — becomes that crawler's `Outcome`:

- a `CrawlError` gives `crawl = exc.crawl` and `error = exc.__cause__` (the
  original failure);
- anything else gives `crawl = None` and `error = exc`.

The other crawlers carry on. A `BaseException` that is not an `Exception`
(`KeyboardInterrupt`, `CancelledError`) is not caught.

### Leaving early

When the generator is closed before every crawler has finished — the caller
`break`s — its `finally` cancels the unfinished tasks and waits for them, so
their sessions close and `closed()` runs with `reason == 'cancelled'`. As with
`Crawl.stream()`, Python closes an abandoned async generator only when it
finalises it, so a caller that stops early wraps the call in
`contextlib.aclosing()`. A run consumed to the end needs nothing.

### Layout

- `collector/engine/many.py` — new: `Outcome`, `crawl_many()` and a private
  per-crawler coroutine. Built entirely on `open_crawl()`; no other module
  changes apart from the exports in `collector/engine/__init__.py` and
  `collector/__init__.py`.

## Testing

`tests/test_many.py`, no network, with `build_http_client` replaced by a fake
as in `tests/test_runner.py`:

- one `Outcome` per crawler, in completion order (steered by `consume`
  sleeping for different times);
- `concurrency` is respected (the peak number of `consume` calls in flight);
- a crawler whose `parse()` raises: `error` is the original exception,
  `crawl.stats.errors == 1`, and the other crawlers succeed;
- an exception from `consume`, and one from building the client (`crawl is
  None`), are isolated the same way;
- bad `params` for one class raise `ValueError` naming it, before the client
  factory is called at all;
- without `consume`, items reach `process_item()`;
- `log` receives `(name, message)`; the default goes to the logger as
  `[name] message`;
- breaking out inside `aclosing()` cancels unfinished crawlers, whose
  `closed()` sees `reason == 'cancelled'`;
- `concurrency=0` raises `ValueError`.

`examples/many.py` runs two small crawlers against quotes.toscrape.com at once
and prints a line per finished crawler and a total; it is listed in
`examples/README.md`, and README and CHANGELOG get an entry.

## Consequence for `trading_platform`

Outside this repository: `crawl_one`, the semaphore and the `as_completed`
loop in `run_all` are replaced by `crawl_many(..., consume=...)`, where
`consume` opens the site's store and counts new and updated lots. Discovery,
the progress line and the summary table stay in the application — and the
table can now show a failed site's stats from `outcome.crawl.stats`.
