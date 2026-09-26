# Item storage: `Dataset`

## Problem

The framework hands items over and keeps none: `process_item()` pushes them to
whatever the application put in `ctx.sink`, `stream()` pulls them, and
`crawl_many(consume=...)` gets the open crawl. Every application that wants
to look at what it collected — during development, most of all — writes the
same few lines to put items somewhere, and the README lists "no storage"
among the things left out by design.

This reverses that, for one kind of storage. Crawlee (`crawlee-python`) was
the reference: it has three storages — `Dataset` (append-only items),
`KeyValueStore` and `RequestQueue` — over pluggable backends (memory, a file
per item, SQL, Redis), and a handler writes with `push_data(item)`. Only the
`Dataset` is taken here.

TinyDB was considered for the file backend and rejected: it rewrites its whole
JSON file on every insert. `geo-info/trading_platform` measured it before
moving to MongoDB — one upsert grew from 11 ms to 210 ms over 439 items, 44 s
of CPU for one site's run, and a synchronous write blocks the event loop every
concurrent crawl shares. SQLite is in the standard library, an insert does not
get dearer as the file grows, and it can be queried while a crawl is writing.

## Goals

- Pass a dataset to a run and every item the crawl emits is stored, with the
  name of the crawler that emitted it, without changing the crawler.
- One interface an application implements for its own backend.
- An in-memory dataset for tests and scripts, and a SQLite one for looking at
  what a crawl collected.
- Export to JSON, JSON Lines or CSV.

## Non-goals

- `KeyValueStore` and `RequestQueue`, and so resuming an interrupted crawl.
- Updating items by key (upsert). A dataset only appends.
- Backends with external dependencies (Postgres, Redis, MongoDB): an
  application implements `Dataset` for those.
- A dataset declared on the crawler class. It is the caller's choice, like
  `sink` and `log`.
- Purging on start. A SQLite file is appended to across runs; deleting the file
  starts over.

## Design

### The interface

`collector/storage/base.py`:

```python
class Dataset(ABC):
    @abstractmethod
    async def push_data(self, item: Any, *, crawler: str) -> None: ...

    @abstractmethod
    def iterate_items(self, *, crawler: str | None = None) -> AsyncIterator[Any]: ...

    async def flush(self) -> None: ...  # no-op by default

    async def close(self) -> None: ...  # flush() by default

    async def export_to(self, path: str | Path, *, crawler: str | None = None) -> int: ...

    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, *exc_info) -> None: ...  # close()
```

- `push_data` stores one item, recording which crawler (`Crawler.name`)
  emitted it.
- `iterate_items` yields items in the order they were stored, all of them or
  only one crawler's.
- `flush` makes whatever is buffered durable; `close` releases the backend.
- `export_to` is implemented once, on the base class, over `iterate_items()`,
  and returns the number of items written:
  - `.json` — one JSON array;
  - `.jsonl` — one JSON object per line;
  - `.csv` — columns are the union of the items' top-level keys in the order
    first seen; a nested value is written as its JSON; an item that is not a
    mapping is written under a single `value` column;
  - any other extension raises `ValueError` before anything is written.

  JSON is written with `ensure_ascii=False` and UTF-8, and values JSON does not
  know are encoded as in SQLite below.

### Wiring it into a run

- `open_crawl`, `crawl`, `run_crawler`, `collect` and `crawl_many` take
  `dataset: Dataset | None = None` and pass it to `Crawl(crawler,
  dataset=dataset)`. `crawl_many` passes the same one to every crawler.
- For each item, `Crawl._handle` calls `crawler.process_item(item)`, then
  `dataset.push_data(item, crawler=crawler.name)`, then hands the item to
  `stream()`. `stats.items` is counted as today.
- `Crawl.run()` calls `dataset.flush()` in its `finally`, after the workers
  have stopped and before `crawler.closed()`, so a batch still buffered is
  written even when the application never closes the dataset.
- The caller owns the dataset — opens and closes it. The engine never closes
  it, since several crawls may share one.
- A `push_data` that raises is a failure of the request whose callback emitted
  the item — collected in `crawl.errors` like a `process_item()` that raises.
  A `flush()` that raises at the end fails the crawl.

### `MemoryDataset`

`collector/storage/memory.py`. Keeps `(crawler, item)` pairs in a list and
the items as they are, with no JSON round trip: `iterate_items()` yields the
objects the crawler emitted. It iterates over a snapshot, so reading during a
crawl is safe.

### `SqliteDataset`

`collector/storage/sqlite.py`. `SqliteDataset(path, *, batch_size=100)`.

- Table `items(id INTEGER PRIMARY KEY, crawler TEXT NOT NULL, stored_at TEXT
  NOT NULL, item TEXT NOT NULL)` and an index on `crawler`. `stored_at` is the
  UTC time the item was pushed, ISO 8601; `item` is its JSON.
- All SQLite work runs on one dedicated thread (a `ThreadPoolExecutor` with one
  worker): a `sqlite3` connection may not move between threads, and the event
  loop must not block on disk. The file is opened, with `journal_mode=WAL` so
  it can be read from the `sqlite3` shell during a crawl, on the first
  operation.
- `push_data` buffers; a full batch (`batch_size` items), `flush()` and
  `close()` write the buffer in one transaction.
- `iterate_items()` flushes first, then reads rows in `id` order, 1000 at a
  time.
- An existing file is appended to.
- `close()` flushes, closes the connection and shuts the thread down. A second
  `close()` does nothing; a `push_data` after `close()` raises `RuntimeError`.
- Encoding: `json.dumps(item, ensure_ascii=False, default=...)`, where the
  default turns a `date`/`datetime` into `isoformat()` and anything else into
  `str()`. Refusing an item with a `datetime` in it would make the debugging
  store fail on exactly the models it is for.

### Exports

`collector.storage` exports `Dataset`, `MemoryDataset` and `SqliteDataset`.
The package root exports `Dataset`, `MemoryDataset` and `SqliteDataset` too:
the person starting a run is the one who picks one, as with `crawl_many`.

## Testing

`tests/test_storage.py`, parametrised over both datasets where the behaviour is
shared:

- items come back in the order pushed; filtering by crawler;
- `export_to` for `.json`, `.jsonl` and `.csv` writes the expected content and
  returns the count; an item that is not a mapping in CSV; an unknown
  extension raises `ValueError` and writes nothing;
- Cyrillic survives a round trip.

SQLite only:

- `date` and `datetime` are stored as ISO strings, other unknown types via
  `str()`;
- `iterate_items()` sees a batch not yet flushed;
- a reopened file sees what an earlier instance wrote;
- `close()` twice is fine; `push_data` after `close()` raises.

Engine (`FakeHttp`):

- a crawl with a dataset stores every item under the crawler's name;
- `flush()` is called once at the end of a crawl;
- a `push_data` that raises lands in `crawl.errors`;
- `stream()` and the dataset see the same items;
- `crawl_many` with one dataset tags each crawler's items with its name.

## Documentation

- README: "no storage" leaves "What you do not get, by design"; the sentences
  saying the framework never persists anything or stores nothing (README,
  `collector/__init__.py`, `Crawler.process_item`, `examples/pipeline.py`)
  say instead that it stores nothing unless given a `Dataset`; a bullet
  describes `Dataset`, the two implementations and `export_to`.
- `examples/storage.py`: quotes into a `SqliteDataset`, then exported to CSV;
  listed in `examples/README.md`.
- CHANGELOG: Added.
