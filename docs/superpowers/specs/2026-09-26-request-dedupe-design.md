# Request de-duplication

## Problem

A crawl sends every request it is handed, including ones it has already sent.
A listing whose rows shift while it is being paged — new lots pushing old ones
onto the next page — puts the same lot link on two pages, and the lot page is
fetched twice. Pages that link to each other (`examples/tuning.py`) are fetched
until `max_requests` runs out. Nothing in a crawler can prevent it short of
keeping its own set of seen URLs.

The README lists "no request de-duplication" among the things the framework
leaves out by design. This reverses that: a repeated request is almost always
a mistake rather than an intention, and every crawler ends up writing the same
set otherwise.

Crawlee (`crawlee-python`) was the reference: `Request.unique_key` — a
normalised URL by default, `METHOD|hash(headers)|hash(payload)|url` with
`use_extended_unique_key`, overridable per request, bypassed with
`always_enqueue` — checked by its `RequestQueue`, which also persists for
resuming. Two things differ here, deliberately.

- Crawlee's default key is the URL alone, so two POSTs to one URL with
  different bodies are one request. ASP.NET WebForms pagination (the iTender
  crawlers in `geo-info/trading_platform`) is exactly that — every page is a
  POST to the listing URL with a different `__EVENTTARGET` — and would stop at
  page one. Here the method and body are part of the default key.
- Crawlee strips a trailing slash and `utm_*` parameters. Both can change which
  page a URL names; the key here does not rewrite what the URL means.

## Goals

- By default, a request whose key was already seen in this crawl is not sent.
- A key that tells POST pagination pages apart without any help.
- A way to send one request regardless, a way to give a request its own key,
  and a way to switch the whole thing off per crawler.
- A count of what was dropped.

## Non-goals

- Remembering keys across runs, or a persistent request queue to resume from.
  That belongs with storage, a separate piece of work.
- A pluggable key function per crawler. `unique_key` on a request covers the
  cases seen so far.
- Headers or cookies in the key.

## Design

### The key

`request_key(request: Request) -> str`, a pure function in
`collector/crawler/request.py`:

- `request.unique_key`, if set, is the key as it is.
- Otherwise `f'{METHOD}|{url}|{body}'`:
  - `METHOD` — `request.method.upper()`;
  - `url` — the request URL with the scheme and host lower-cased, the
    fragment dropped, `request.params` merged into the query, and the query
    pairs sorted. The path, a trailing slash and every query parameter are
    kept as they are;
  - `body` — empty when the request has neither `data` nor `json`; otherwise
    the sha256 hex digest of a canonical form: `data` as a dict → its items
    sorted, as a list of pairs → as given, as a string → as given; `json` →
    `json.dumps(json, sort_keys=True, separators=(',', ':'))`. `data` and
    `json` are hashed together when both are set.

So `?a=1&b=2`, `?b=2&a=1` and `params={'b': 2, 'a': 1}` share a key; `/lots`
and `/lots/` do not; a GET and a POST to one URL do not; two POSTs to one URL
with different bodies do not.

### The request

`Request` gains two fields, neither of which reaches the transport
(`http_kwargs()` is unchanged):

- `unique_key: str | None = None` — replaces the computed key;
- `dont_filter: bool = False` — send this request even if its key was seen.

`Crawler.request()`, `Response.follow()` and `Response.form_request()` accept
and forward both.

### The check

`Crawl` keeps the keys of the requests it has queued in this run. Every
request that is queued — from `start_requests()` and from a callback — goes
through one place (`Crawl._enqueue`):

- `Settings.dedupe` false: queued, nothing recorded.
- `dont_filter` true: queued, and its key recorded, so a plain request for the
  same page afterwards is a duplicate.
- key not seen: queued and recorded.
- key seen: dropped. `stats.duplicates` goes up by one and the drop is logged
  at DEBUG (`crawl.duplicate METHOD URL`). A dropped request never reaches the
  queue, so it is not in `stats.requests` and does not count toward
  `max_requests`.

Retries inside the HTTP client and `retry()` from a response hook are the same
request sent again, not a new one queued, and are not affected.

### Settings and stats

- `Settings.dedupe: bool = True`, in the pacing group next to `max_requests`.
- `Stats.duplicates: int = 0`.

### Exports

`request_key` is exported from `collector.crawler`, for a test or an
application that wants to see the key; the package root is unchanged — a
crawler author writes `unique_key=` and `dont_filter=`, not keys.

### Behaviour change

A crawler that sends the same request twice today will send it once. The
CHANGELOG says so under a **Behaviour change** heading, with
`Settings(dedupe=False)` as the way back.

## Testing

`tests/test_request_key.py` (pure):

- query order does not matter; `params` merge into the URL's query;
- the fragment is dropped; the host's case does not matter; a trailing slash
  does;
- the method matters;
- the same `data` dict in a different order gives the same key; a different
  body gives a different key; a list of pairs and `json` are both hashed;
- `unique_key` replaces the computed key;
- headers and cookies do not change the key.

`tests/test_crawl.py` (with the existing `FakeHttp`):

- two links to one page: one fetch, `stats.duplicates == 1`;
- a duplicate among the start URLs is dropped too;
- `dont_filter=True` is fetched again;
- `Settings(dedupe=False)` fetches every duplicate;
- two POSTs to one URL with different bodies are both fetched;
- duplicates do not use up `max_requests`.

## Documentation

- README: "no request de-duplication" leaves the "What you do not get, by
  design" list (storage stays in it for now), and a bullet describes the key,
  `dont_filter`, `unique_key` and `Settings(dedupe=False)`.
- `examples/tuning.py`: the `max_requests` comment "nothing de-duplicates" is
  no longer true and is reworded.
- CHANGELOG: Added (`Request.unique_key`, `Request.dont_filter`,
  `Settings.dedupe`, `Stats.duplicates`, `request_key`) and the behaviour
  change.
