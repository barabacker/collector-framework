# Examples

Eleven runnable scripts, each about one thing. Read them in this order if you are
new to the framework; reach for one by name otherwise.

```bash
uv run python examples/quotes.py
```

| | Shows |
| --- | --- |
| [`quotes.py`](quotes.py) | Pagination: follow a chain, emit an item per quote. The shape most scrapes have. |
| [`json_api.py`](json_api.py) | A JSON API: per-request `params`, a second callback for a differently-shaped page, `metadata` riding along. |
| [`forms.py`](forms.py) | `form_request()`: post a page's own form back — hidden CSRF token included — with a few fields overridden. |
| [`streaming.py`](streaming.py) | `open_crawl()` + `stream()`: take items as they arrive, `break` when you have enough, and the crawl stops with you. |
| [`many.py`](many.py) | `crawl_many()`: several crawlers at once, capped, each one's items consumed on their own and an outcome per crawler as it finishes. |
| [`pipeline.py`](pipeline.py) | Components declared in Settings transform items in order before collect receives them. |
| [`storage.py`](storage.py) | An application-owned JSONL pipeline: configuration, opening, asynchronous writes and closing. |
| [`mongo.py`](mongo.py) | A MongoDB pipeline with an optional async driver, settings, connection check and client cleanup. |
| [`errors.py`](errors.py) | `max_errors`: the first failure stops a crawl by default; a tolerance lets one with bad pages succeed — and every failure leaves on `crawl.errors`, or on a `CrawlError` as `exc.crawl.errors`. |
| [`hooks.py`](hooks.py) | Request and response hooks, including one that answers a 401 by authenticating the session and asking again. |
| [`tuning.py`](tuning.py) | `Settings`: pacing, concurrency, limits and retries, how a run's `params` override three of them, and a crawler's own declared `params`. |

## They use the network

`quotes.py`, `json_api.py`, `forms.py`, `streaming.py`, `many.py`, `storage.py`, `mongo.py`
and `pipeline.py` crawl [quotes.toscrape.com](https://quotes.toscrape.com/), a
sandbox that exists to be scraped. `errors.py`, `hooks.py` and `tuning.py` use
[mockhttp.org](https://mockhttp.org), an httpbin clone. Both are someone else's
machines, so every example here declares a `delay` and a `max_requests` where a
crawl could otherwise run long.

Importing one of these modules does nothing — each keeps its crawl behind
`if __name__ == '__main__':`, which is also what lets the test suite check they
still match the API without going near a socket.

`storage.py` appends to `quotes.jsonl` in the working directory. `pipeline.py`
prints transformed items. Neither example deletes existing results.

## MongoDB example

`mongo.py` uses [PyMongo Async](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/).
The driver is an optional example dependency, not a collector dependency:

```bash
uv run --with 'pymongo>=4.13' python examples/mongo.py
```

Run this only when you intend to scrape the sample site and insert results into
your MongoDB. Defaults: `mongodb://localhost:27017`, database `collector_examples`,
collection `quotes`. Override with environment variables `MONGO_URI`,
`MONGO_DATABASE`, `MONGO_COLLECTION`, or reuse the component with your own
`Settings.pipeline_options`. The collection defaults to the crawler's name.

The component checks the connection before crawling and closes its client on
completion, failure or cancellation. It copies each mapping before insertion
so the driver's generated `_id` does not mutate the item sent to the stream.
Items must contain BSON-compatible values. Each run appends documents; repeated
runs may create duplicates, and caller-supplied duplicate `_id` values fail.
No database, collection or document is deleted by the example.
