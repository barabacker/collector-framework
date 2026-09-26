# Examples

Nine runnable scripts, each about one thing. Read them in this order if you are
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
| [`pipeline.py`](pipeline.py) | `process_item()` and `sink`: writing items somewhere, and reading the run's own counters back off `crawl.crawler`. |
| [`errors.py`](errors.py) | A bad page is collected, not fatal — and every failure leaves on a `CrawlError` as `exc.crawl.errors`. |
| [`hooks.py`](hooks.py) | Request and response hooks, including one that answers a 401 by authenticating the session and asking again. |
| [`tuning.py`](tuning.py) | `Settings`: pacing, concurrency, limits and retries, and how a run's `params` override two of them. |

## They use the network

`quotes.py`, `json_api.py`, `forms.py`, `streaming.py`, `many.py` and `pipeline.py` crawl
[quotes.toscrape.com](https://quotes.toscrape.com/), a sandbox that exists to be
scraped. `errors.py`, `hooks.py` and `tuning.py` use
[mockhttp.org](https://mockhttp.org), an httpbin clone. Both are someone else's
machines, so every example here declares a `delay` and a `max_requests` where a
crawl could otherwise run long.

Importing one of these modules does nothing — each keeps its crawl behind
`if __name__ == '__main__':`, which is also what lets the test suite check they
still match the API without going near a socket.

`pipeline.py` writes `quotes.jsonl` into the working directory.
