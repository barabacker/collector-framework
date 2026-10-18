# Moving persistence to application pipelines

This is an incompatible API change. `collector.storage`, `Dataset`,
`MemoryDataset`, `SqliteDataset`, `Crawl.dataset` and the `dataset=` argument of
`open_crawl`, `crawl`, `run_crawler`, `collect` and `crawl_many` are removed.
Old imports fail, and passing `dataset=` raises TypeError. No compatibility
adapter or automatic conversion silently changes where results are stored.

Previously:

```python
async with SqliteDataset('results.db') as dataset:
    await crawl(MyCrawler, dataset=dataset)
    await dataset.export_to('results.csv')
```

Now declare an application component in crawler settings:

```python
class SaveItems:
    @classmethod
    def from_crawler(cls, crawler):
        component = cls()
        component.options = crawler.settings.pipeline_options
        component.source = crawler.name
        return component

    async def open_crawler(self): ...  # Open your application's storage.

    async def process_item(self, item):
        ...  # Store using the application's schema and source identifier.
        return item

    async def close_crawler(self): ...  # Flush and close your application's storage.


class MyCrawler(Crawler):
    settings = Settings(
        pipelines=(SaveItems,),
        pipeline_options={'path': 'results.jsonl'},
    )
    # start_urls, parse(), etc.


await crawl(MyCrawler)
```

`examples/storage.py` is a complete JSONL component. It is example application
code, not a bundled storage backend. You can write a SQLite or Mongo component
using your own dependency and schema; collector imports neither. Existing
`ctx.sink` and `crawler.process_item` remain supported, but the latter follows
the request error budget. Pipeline errors are always fatal to their own crawl.

Use `collect()` for a short run's processed items in memory, or consume
`stream()` for incremental handling. A pipeline passes its returned item to
the next component and then the stream; returning None emits None, not a drop.
`stats.items` counts callback emissions, not successful commits. There is no
automatic retry, transaction, deduplication of stored items or rollback. Move
existing saving code out of the consumer if the pipeline now saves it.

Every crawl owns a separate set of pipeline instances. An application that used
one shared Dataset must now explicitly coordinate a shared writer, or use
separate resources/paths. Do not close a shared client from each component.
Store mutable state in the component; settings/options are read-only configuration.

Resources open before crawler.opened/start_requests and stay open until workers
stop and crawler.closed completes. Components whose open fails must release
their partial setup. Synchronous methods run on the event loop; applications
must make blocking storage I/O asynchronous and await pending writes before close.
Multiple workers may process items concurrently. See README for cancellation
and failure handling; cleanup errors never replace the primary exception.

## Reading and exporting existing data

Existing SQLite files are untouched. Keep the previous library version available
in a separate environment if its readers/exporters are needed, or implement a
reader in your application using sqlite3. The previous table was
`items(id, crawler, stored_at, item)`, with JSON in `item`; for example:

```sql
SELECT crawler, item FROM items ORDER BY id;
```

The application decides how to deserialize values, filter by crawler and export
CSV/JSON. New pipeline output does not have to use the old schema; choose it
explicitly if you want to append compatibly. No migration or ETL runs as part
of installing or importing collector.

This transition supersedes the old Dataset storage design in
`docs/superpowers/specs/2026-09-26-dataset-storage-design.md`. That document is
retained as history. Completeness reporting, public testing helpers, HTTP status
helpers and inprotect remain separate proposals.
