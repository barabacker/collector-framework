# Crawler terminology rename

## Problem

The project spreads four related concepts — `collector`, `spider`, `Parser`,
`Crawler` — across package names, directory names and class names without a
consistent mapping between them:

- `collector.spider` is a package whose name promises a `Spider` class, but it
  exports `Parser`, `Request` and `Response`. `Spider` appears only as a
  stylistic aside in docstrings ("Spider-style parser").
- `collector.engine` exports a class called `Crawler`, which is a different
  thing from the `Parser` a scraper author subclasses: `Parser` is the
  declarative class an author writes, `Crawler` is the engine object that
  runs one and holds its queue, stats and errors.

Nothing here is a bug — the framework works — but the names don't tell a
reader which word means "what I write" and which means "what runs it",
particularly since `spider` (a package) implies a class that isn't there.

## Goals

- Reduce the vocabulary to two central terms that map onto the two roles a
  user actually needs to know: the class they subclass, and the object a run
  gives back.
- Make package names match what they contain.
- Keep `Request` / `Response` and the `parse()` callback name unchanged —
  they aren't part of the confusion and `parse()` matches the Scrapy
  convention this framework already follows.

## Non-goals

- No behavior change. This is a pure rename across identifiers, file names,
  and prose (docstrings, README, pyproject description).
- Past `CHANGELOG.md` entries are not rewritten — they're a historical record
  of what things were called at each point in time. A new entry is added
  instead.

## Design

### Renaming table

| Before | After |
|---|---|
| `Parser` (class an author subclasses) | `Crawler` |
| `ParserContext` | `CrawlerContext` |
| `Crawler` (engine: queue, workers, stats) | `Crawl` |
| `collector.spider` (package) | `collector.crawler` |
| `engine/crawler.py` (file, held the engine `Crawler`) | `engine/crawl.py` (holds `Crawl`) |
| `run_parser()` | `run_crawler()` |
| `open_crawler()` | `open_crawl()` |
| `crawl()` (async entry point, returns the run object) | unchanged — now literally returns a `Crawl` |
| `collect()` | unchanged |
| `parser_cls` (parameter/variable name) | `crawler_cls` |
| `Crawler.parser` (engine's field holding the author's instance) | `Crawl.crawler` |
| `exc.crawler` (attached to a raised exception) | `exc.crawl` |
| `Parser.parse()` | unchanged — stays `Crawler.parse()` |
| `Request`, `Response` | unchanged |

After the rename, `collector.spider` is `collector.crawler` — the package a
scraper author writes against — mirroring the existing, already-idiomatic
pattern in `collector.engine`, where a package name (`engine`) differs from
the class defined in a same-named file (`crawler.py` → `Crawler`). The same
pattern (`crawler/crawler.py` → `Crawler`) is not a new kind of stutter for
this codebase; it already exists as `engine/crawler.py` → `Crawler` today,
and Scrapy establishes the same pattern in the wild (`scrapy.spiders.Spider`).

### File-by-file scope

- `src/collector/spider/` → `src/collector/crawler/`
  - `parser.py` → `crawler.py`: `Parser`→`Crawler`, `ParserContext`→`CrawlerContext`
  - `request.py`, `response.py`: unchanged code; docstring prose ("the
    parser's `parse()`") updated to "the crawler's `parse()`"
  - `__init__.py`: docstring and exports updated
- `src/collector/engine/`
  - `crawler.py` → `crawl.py`: class `Crawler`→`Crawl`; internal field
    `parser: Parser`→`crawler: Crawler`; all internal uses of `self.parser`→
    `self.crawler`
  - `runner.py`: `open_crawler`→`open_crawl`, `run_parser`→`run_crawler`,
    `parser_cls` params→`crawler_cls`, `_attach_crawler`→`_attach_crawl`,
    `exc.crawler`→`exc.crawl`, docstrings and the assembly diagram in the
    module docstring
  - `params.py`: docstring prose only (refers to `Crawler`, now `Crawl`)
  - `__init__.py`: exports updated (`Crawl` replaces `Crawler`)
- `src/collector/http/client.py`: `parser_cls`→`crawler_cls` (parameter and
  internal uses), prose ("the parser's own hooks" → "the crawler's own
  hooks", "next to the parser that needs it" → "next to the crawler that
  needs it"), import path updated to `collector.crawler.crawler`
- `src/collector/settings.py`: docstring prose only
- `src/collector/__init__.py`: module docstring rewritten for the new
  vocabulary; imports/exports updated (`Crawler`, `CrawlerContext`, `Crawl`,
  `run_crawler`, `open_crawl`, `crawl`, `collect`, `Stats`, ...)
- `tests/`
  - `test_parser.py` → `test_crawler.py` (tests the author-facing class,
    now named `Crawler`)
  - `test_crawler.py` → `test_crawl.py` (tests the engine class, now named
    `Crawl`) — done in the same change so the two renames don't collide
  - `test_runner.py`, `test_factory.py`, `test_params.py`,
    `test_mockhttp.py`, `test_examples.py`, `test_package.py`,
    `conftest.py`: identifiers and fixtures updated wherever they reference
    the renamed symbols
- `examples/*.py`: `class Quotes(Parser)` → `class Quotes(Crawler)`, callers
  of `run_parser`/`open_crawler` updated; `async def parse(self, response)`
  stays as-is
- `README.md`: code samples and the "What you get" section updated to the
  new names
- `pyproject.toml`: `description` field drops "Spider-style parsers" (no
  `Spider` class exists) in favor of language matching the new vocabulary
  (e.g. "declarative crawlers"); the `keywords` list keeps `"spider"` — it's
  a PyPI discovery term, not an identifier, and scrapers are still commonly
  searched for under that word
- `CHANGELOG.md`: no historical entries are edited. One new entry is added
  under the existing `## [Unreleased]` / `### Changed` section, in the same
  style as the existing `` `BaseParser` is now `Parser` `` entry, describing
  the full rename and its rationale.

## Verification

After the rename, `git grep` for the old identifiers across the non-history
surface should return nothing:

```
git grep -n "Parser\|ParserContext\|run_parser\|open_crawler\|collector\.spider" -- src tests examples README.md
```

(`CHANGELOG.md` is excluded deliberately — its past entries keep the old
names.) The test suite must pass unchanged (no behavior changed, only
names).
