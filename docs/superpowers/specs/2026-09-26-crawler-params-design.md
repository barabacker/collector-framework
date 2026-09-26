# Typed per-run crawler parameters

## Problem

A crawler has no place to declare the knobs a run can turn. `params` on the
entry points is a free-form `dict[str, str]` that lands on `ctx.params`; the
engine reads two keys out of it (`concurrency`, `max_requests`), and anything
else a crawler wants it has to find, parse and validate by hand.

The iTender crawlers in `geo-info/trading_platform` show where that leads.
They have two per-run knobs — how many listing pages to walk (`MAX_PAGES`) and
a date window (`SINCE`) — and keep them as class attributes, because nothing
else is typed. Their `run_all` then overrides a run's values by assigning to
the class:

```python
for p in platforms:
    if args.max_pages is not None:
        p.parser_cls.MAX_PAGES = args.max_pages
    if args.since is not None:
        p.parser_cls.SINCE = args.since
```

That mutates shared state for the rest of the process, cannot give two
concurrent runs of one crawler different values, and leaves a typo or a bad
value to fail — or, worse, not fail — somewhere in the middle of a crawl.

## Goals

- A crawler declares its parameters, with types and defaults, in one place.
- A run passes values through `params=`, as strings (a CLI flag, a job payload)
  or as already-typed values, and the crawler reads them typed from
  `self.params`.
- A bad value or an unknown name fails before the first request, naming what
  was wrong.
- A crawler that declares nothing behaves exactly as today.

## Non-goals

- Changing how the engine's own two knobs are read. `concurrency` and
  `max_requests` keep their forgiving rule (log and fall back); making them
  strict is a separate, breaking change.
- Types beyond the scalar set below: no lists, enums, nested dataclasses.
- Generating a CLI from the declaration. The declaration makes that possible
  (`dataclasses.fields`), but building it is the application's business.
- A dependency on pydantic or anything else.

## Design

### Declaring

```python
@dataclass(frozen=True)
class FogsoftParams:
    max_pages: int = 100
    since: date | None = None


class TenderFogsoft(Crawler):
    params = FogsoftParams()


class Tiny(TenderFogsoft):
    params = replace(TenderFogsoft.params, max_pages=5)
```

`Crawler.params` is an *instance* of a dataclass, not the class: it carries the
crawler's defaults, and a subclass narrows them with `dataclasses.replace` —
the same pattern `settings = Settings(...)` already uses. It defaults to
`None`, meaning "no declared parameters".

The declaration is checked when the crawler class is defined
(`Crawler.__init_subclass__`), so a mistake surfaces on import rather than on
the first run. `TypeError` if:

- `params` is neither `None` nor a dataclass instance;
- a field's type is not one of the supported types below;
- a field is named `concurrency` or `max_requests` — those names belong to the
  engine.

### Reading

During a run `self.params` is a new instance: the declared one with the run's
overrides applied (`replace(declared, **overrides)`). The class attribute is
never modified, so two concurrent runs of one crawler with different values do
not see each other's.

`ctx.params` keeps the raw mapping the run was given; the engine still reads
its two knobs from there.

### Converting

Field types are read with `typing.get_type_hints()`, so string annotations
under `from __future__ import annotations` resolve. For each key in the run's
`params` that names a field:

| Field type | From a string | Already-typed value |
|---|---|---|
| `str` | as is | must be `str` |
| `int` | `int(value)` | must be `int` and not `bool` |
| `float` | `float(value)` | `int` or `float` (not `bool`), stored as `float` |
| `bool` | `true`/`false`/`1`/`0`/`yes`/`no`, case-insensitive | must be `bool` |
| `date` | `date.fromisoformat` | must be a `date` that is not a `datetime` |
| `datetime` | `datetime.fromisoformat` | must be `datetime` |
| `X \| None` | `''` → `None`, otherwise as `X` | `None`, or as `X` |

Any other combination — a string that does not parse, a value of the wrong
type — is a bad value.

### Errors at run time

Only when the crawler declares `params`:

- a bad value: `ValueError` naming the parameter, the value (`repr`) and the
  expected type, e.g. `params['since']: expected date, got '2026-13-01'`;
- a key that is neither a field nor an engine knob: `ValueError` naming the key
  and listing the known names, e.g.
  `unknown param 'max_page'; known: concurrency, max_pages, max_requests, since`.

When the crawler declares nothing, `params` is free-form as today: any key is
accepted and left on `ctx.params`, and `self.params` is `None`.

`open_crawl()` resolves the parameters before it builds the HTTP client, so
these errors leave without a session having been opened and without a request
sent. They are configuration errors, not failed crawls, so they are raised as
they are and not wrapped in `CrawlError`. The same applies to `crawl()`,
`run_crawler()` and `collect()`, which go through `open_crawl()`.

### Layout

- `collector/crawler/params.py` — new, in the `crawler` package because the
  crawler declares the parameters:
  - `check_declaration(crawler_cls) -> None`, called from
    `Crawler.__init_subclass__`;
  - `resolve_params(declared, raw: Mapping[str, Any]) -> declared's type | None`
    — pure: returns `None` when `declared is None` (and then checks nothing),
    otherwise the declared instance with the converted overrides applied, or
    raises `ValueError`.
- `Crawler.params: Any = None` on the class; `Crawler.__init__` sets
  `self.params = resolve_params(type(self).params, ctx.params)`, so a crawler
  constructed directly — as the tests do — gets typed parameters too.
- `open_crawl()` calls `resolve_params(crawler_cls.params, params)` before
  `build_http_client()`, only so that it fails early; the result is discarded,
  and `Crawler.__init__` stays the one place parameters reach a crawler.
- `params=` on `open_crawl`, `crawl`, `run_crawler` and `collect` widens from
  `dict[str, str] | None` to `Mapping[str, Any] | None`, and
  `CrawlerContext.params` from `dict[str, str]` to `Mapping[str, Any]`. The
  engine readers in `engine/params.py` already call `int()` on what they find,
  which accepts an `int` as well as a string; their annotations widen to match.
- `engine/params.py` keeps reading only the engine's two knobs; its docstring
  stays true.

## Testing

`tests/test_crawler_params.py`, no network:

- conversion from strings for every supported type, including `X | None` with
  `''`;
- already-typed values pass through; `True` for an `int` field is rejected; a
  `datetime` for a `date` field is rejected;
- a field absent from the run's `params` keeps the declared default;
- a subclass that narrows with `replace` gets its own defaults;
- a bad value and an unknown key raise `ValueError` with the messages above;
- `concurrency` and `max_requests` are accepted alongside declared fields;
- `TypeError` at class definition for a non-dataclass `params`, an unsupported
  field type and a reserved field name;
- a crawler without a declaration accepts arbitrary keys, leaves them on
  `ctx.params` and has `self.params is None`;
- the class attribute is unchanged after a run with overrides;
- `open_crawl()` with a bad value raises before `build_http_client()` is called
  (the test replaces it with one that fails if called).

`examples/tuning.py`, which already covers `params`, gains a crawler with a
declared `params`; README and CHANGELOG get an entry.

## Consequence for the iTender crawlers

Outside this repository:

```python
@dataclass(frozen=True)
class FogsoftParams:
    max_pages: int = config.max_pages
    since: date | None = config.since


class TenderFogsoft(Crawler):
    params = FogsoftParams()
    ...
    if num_page >= self.params.max_pages:
        ...
```

and `run_all` passes the run's values instead of assigning to the class:

```python
overrides = {
    k: v for k, v in {'max_pages': args.max_pages, 'since': args.since}.items() if v is not None
}
async with open_crawl(platform.parser_cls, params=overrides, log=log) as crawl:
    ...
```
