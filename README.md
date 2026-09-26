# collector

A tiny async scraping framework: declarative crawlers over a `curl_cffi` HTTP
layer with hooks, retries and throttling. Small enough to read in one sitting,
and it stays out of your domain model.

```python
from collector import Crawler, Settings, collect


class Quotes(Crawler):
    name = 'quotes'
    start_urls = ['https://quotes.toscrape.com/']
    settings = Settings(concurrency=4, delay=0.5)

    async def parse(self, response):
        for quote in response.selector().css('div.quote'):
            yield {
                'text': quote.css('span.text::text').get(),
                'author': quote.css('small.author::text').get(),
            }

        next_page = response.selector().css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page)


for quote in collect(Quotes):
    print(quote['author'])
```

## Why it exists

`Scrapy` brings a whole runtime (its own event loop, settings, signals, project
layout) and `requests`-in-a-loop brings none. This sits in between: an async
producer/consumer crawl you can embed in a worker, a CLI or a web job, with
browser impersonation via `curl_cffi` for sites that fingerprint TLS.

## What you get

- **`Crawler`** — declarative: `parse()` is an async generator that yields a
  `Request` to follow or anything else to emit it as an item, and
  `start_requests()` covers a start that a URL cannot express — a POST, or
  per-start metadata. It holds no run state of its own. `opened()` runs once
  before the crawl starts — the place for async setup `__init__` cannot do —
  and `closed(stats)` runs once it is over (cleanly, on `max_requests`,
  cancelled or failed) to release it again; `closed()` only runs if `opened()`
  succeeded, the same rule `async with` follows.
- **`Crawl`** — the engine, and what running a crawler gives back. It owns the
  queue and the `concurrency` workers, collects per-request errors instead of
  killing a worker, and carries on up to `Settings.max_errors` of them before
  stopping and raising the first; it carries `stats`, `errors` and the
  `crawler` itself once the run is over. Through `run_crawler()` / `crawl()` /
  `collect()` / `open_crawl()`, that failure arrives as a `CrawlError` — the
  crawl on `.crawl`, the original failure chained as `.__cause__`.
- **`max_requests`** — a safety valve. Without a ceiling, a bug in pagination
  crawls forever with nothing to stop it; on reaching it the crawl ends
  cleanly with `stats.reason == 'max_requests'`.
- **`on_error(request, exc)`** — an optional hook run when a request fails.
  The framework still collects it and applies its `max_errors` policy either
  way; this is for reacting (a metric, a note), not for changing what
  happens next. A broken override is logged, not left to take the worker
  down with it.
- **`Settings`** — one frozen dataclass per crawler holding proxy, timeout,
  impersonation, headers, TLS quirks, pacing, retry policy and hooks. A subclass
  narrows its parent's with `dataclasses.replace`. The HTTP client is assembled
  from it, so the caller never carries site quirks.
- **One retry policy** — `RetryPolicy` covers both failure modes with a single
  attempt budget: transport errors and retryable statuses (429 and the 5xx
  family), with exponential backoff and `Retry-After` honoured up to a cap you
  set. A response hook can separately `await retry()` to re-run a request, which
  is how an anti-bot challenge gets solved without the crawler knowing.
- **Throttling** — `delay` and `delay_jitter` install a `Throttle` hook that
  spaces requests out behind a lock, so the gap holds with `concurrency > 1`.
  `AutoThrottle` widens a `Throttle`'s delay on a retryable status and narrows
  it back on a normal one, between a floor (the declared `delay`) and a
  `ceiling` you set.
- **Response helpers** — `status`, `text`, `headers`, `json()`, `urljoin()`,
  `follow()` for a link on the page, `form_request()` to submit a form on it
  the way a browser would (hidden fields included, nothing clicked unless
  named), and `selector()` (parsel), which parses the
  body once however often you ask for it.
- **Per-request transport** — a `Request` carries `headers`, `params`, `data`,
  `json` and `cookies`; what is session-wide instead (a proxy, a base header
  set) belongs in `Settings`.
- **Streaming** — `crawl.stream()` yields items as they are produced, over a
  bounded channel, so a slow consumer applies backpressure and `break` stops the
  crawl — `stats.reason` then reads `'cancelled'`, not `'done'`. `open_crawl()`
  owns the HTTP session for as long as the iteration needs it, and stops a crawl
  a consumer walked away from.

```python
async with open_crawl(Quotes) as crawl:
    async for quote in crawl.stream():
        await save(quote)
        if enough():
            break  # the crawl stops with it
    print(crawl.stats)
```

- **Params** — a run's `params` may override `concurrency` and `max_requests`
  without touching the crawler. They arrive as strings from a CLI flag or a job
  payload, so a bad value for those two falls back to what the crawler declared
  and is logged, rather than killing the crawl. A crawler's own knobs — a page
  limit, a date window — are declared as `params`, a frozen dataclass instance
  like `settings`, and read typed from `self.params`; a run's values for those
  are converted from strings and checked before the first request, so a bad
  value or an unknown name fails the run up front.
- **Many crawlers** — `crawl_many(crawlers, concurrency=, params=, consume=,
  log=)` runs a set of crawlers at once, each through `open_crawl()`, at most
  `concurrency` together. `consume(crawl)` handles each crawler's items;
  `log(name, message)` gets every line with the crawler's name. One crawler
  failing lands in its own `Outcome` — with its stats, and the original
  failure rather than the `CrawlError` around it — and the rest carry on;
  outcomes arrive as crawlers finish.
- **De-duplication** — a crawl does not send a request it has already queued.
  Two requests are the same when their method, URL (scheme and host
  lower-cased, fragment dropped, query sorted, `params` merged in) and body
  match — so POSTing one URL with different bodies, as ASP.NET pagination
  does, is several requests. `Request(dont_filter=True)` sends one anyway,
  `Request(unique_key=...)` sets the key, `Settings(dedupe=False)` turns it off,
  and `stats.duplicates` counts what was dropped. Keys live for one crawl.
- **Error policy** — `Settings(max_errors=N)`: up to N failed requests a crawl
  carries on and succeeds, the failures in `crawl.errors` and `stats.errors`;
  one more stops it (`stats.reason == 'max_errors'`, nothing further sent) and
  fails it. `0` by default — the first failure — and `None` for no limit; a run
  can override it with `params={'max_errors': '20'}`.
- **Storage** — pass `dataset=` to any entry point (`crawl_many` included) and
  every item the crawl emits is also written there, under the crawler's name.
  `MemoryDataset` keeps them in a list; `SqliteDataset('run.db')` in a file you
  can query from the `sqlite3` shell while the crawl runs, written in batches
  from its own thread. `iterate_items(crawler=...)` reads them back and
  `export_to('x.json' | 'x.jsonl' | 'x.csv')` writes them out. A SQLite file is
  appended to across runs — delete it to start over. The caller owns the
  dataset and closes it (`async with`); the crawl only flushes it. Implement
  `Dataset` for any other store.

## Examples

Nine runnable scripts in [`examples/`](examples/), each about one thing —
pagination, a JSON API, submitting a form, streaming with an early `break`,
several crawlers at once, writing items to a sink, what happens when pages
fail, hooks that solve a challenge, and the pacing knobs.

```bash
uv run python examples/quotes.py
```

## What you do not get, by design

No item schema, no scheduler, no robots.txt, and no registry — how an
application names and looks up a crawler is its own business, and a library
holding global mutable state for it is a cost, not a feature. Beyond a
`Dataset` you pass in, the framework persists nothing: override
`process_item()` and write to `ctx.sink`, which it passes through untouched.

```python
class Saving(Quotes):
    async def process_item(self, item):
        await self.ctx.sink.save(item)


crawl = run_crawler(Saving, sink=my_sink)
crawl.stats  # Stats(requests=…, errors=…, items=…, reason='done')
crawl.errors  # [(Request, Exception), …]
crawl.crawler  # the instance, for whatever counters the app kept on it
```

`stats.items` is counted by the crawl, so an override that forgets `super()`
cannot corrupt it. `stats.requests` counts requests the crawler asked for —
retries happen inside the HTTP client and are invisible to it.

## Install

```bash
pip install collector-framework     # import name: collector
```

Requires Python 3.11+.

## Status

`0.0.1`, extracted from a production scraper that runs ~30 sites. The API is
young: minor versions may break it until `1.0`.

## License

Apache-2.0
