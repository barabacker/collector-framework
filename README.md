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
  killing a worker, re-raises the first at the end, and carries `stats`,
  `errors` and the `crawler` itself once the run is over. Through
  `run_crawler()` / `crawl()` / `collect()` / `open_crawl()`, that failure
  arrives as a `CrawlError` — the crawl on `.crawl`, the original failure
  chained as `.__cause__`.
- **`max_requests`** — a safety valve. Without a ceiling, a bug in pagination
  crawls forever with nothing to stop it; on reaching it the crawl ends
  cleanly with `stats.reason == 'max_requests'`.
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
  `follow()` for a link on the page, and `selector()` (parsel), which parses the
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
  payload, so a bad value falls back to what the crawler declared and is logged,
  rather than killing the crawl.

## Examples

Seven runnable scripts in [`examples/`](examples/), each about one thing —
pagination, a JSON API, streaming with an early `break`, writing items to a
sink, what happens when pages fail, hooks that solve a challenge, and the
pacing knobs.

```bash
uv run python examples/quotes.py
```

## What you do not get, by design

No item schema, no storage, no scheduler, no request de-duplication, no
robots.txt, and no registry — how an application names and looks up a crawler is
its own business, and a library holding global mutable state for it is a cost,
not a feature. The framework never persists anything: override `process_item()` and
write to `ctx.sink`, which it passes through untouched.

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
