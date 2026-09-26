# Submitting a form from a response

## Problem

A crawler that has to submit an HTML form rebuilds by hand what a browser
does for free. The iTender (fogsoft) crawlers in `geo-info/trading_platform`
are the case that prompted this: every listing page is one ASP.NET WebForms
`<form>`, pagination is a `__doPostBack` that re-posts the page's hidden
tokens, and clearing a default filter means posting the search panel with its
selects reset. `tp/base.py` spends about 80 lines on it —
`extract_initial_tokens`, `build_payload`, `filter_reset_payload` — collecting
hidden inputs, text inputs, selects and the search button field by field.

None of that is specific to those sites. Any WebForms page, and any form that
carries a CSRF token, needs the same thing: "the fields this form would send,
with these few changed".

## Goals

- One call on `Response` that builds the `Request` a browser would send when
  the form is submitted, with named fields overridden.
- The browser's rules for which controls are sent, so a crawler does not have
  to know them.
- Nothing submitted that the crawler did not ask for: no button is clicked
  unless named.

## Non-goals

- Reading a form's structure (its selects and their options, its buttons) as
  an object. A crawler that needs to inspect a form before submitting it keeps
  doing so through `selector()`; only the submission is covered here.
- `multipart/form-data` and file inputs. File inputs are skipped; a form with
  `enctype="multipart/form-data"` is sent urlencoded like any other.
- Executing JavaScript, `formaction`/`formmethod` attributes on buttons, and
  `<input form="…">` controls that live outside their form element.

## Design

### API

```python
class Response:
    def form_request(
        self,
        *,
        form: str | None = None,
        formdata: Mapping[str, str | None] | None = None,
        click: str | None = None,
        callback: Any = None,
        metadata: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Request: ...
```

- `form` — a CSS selector for the form; `None` means the first `<form>` in the
  document. A selector matching several forms takes the first of them.
- `formdata` — fields to override, see below.
- `click` — the `name` of the submit button to press; `None` presses nothing.
- `callback`, `metadata`, `headers` — as on `follow()`.

It is a method on `Response`, not a new export: the package root does not
change.

### What is sent

The fields a browser treats as *successful controls*, in document order,
among the form's descendants:

| Control | Sent |
|---|---|
| any control with the `disabled` attribute, or without a `name` | never |
| `input` of type `submit`, `image`, `button`, `reset`, `file` | never (a submit button only via `click`) |
| `input` of type `checkbox` / `radio` | only when `checked`; `value`, or `"on"` without one |
| any other `input` (including no `type`, `hidden`, `text`, `password`) | its `value`, or `""` |
| `select` | each selected `option`; with none selected, the first `option` unless the select is `multiple` (then nothing) |
| `option` value | its `value` attribute, or its normalised text without one |
| `textarea` | its text |
| `button` | never (only via `click`) |

Fields are a list of `(name, value)` pairs, not a dict: a `select multiple`
and repeated names are legal in HTML and a dict would drop all but one.

### Overrides

`formdata` is applied over the collected fields:

- a name already present has its value replaced **in place**, at the position
  of its first occurrence; any further occurrences of that name are removed;
- a value of `None` removes every field of that name;
- a name the form does not have is appended — this is how `__EVENTTARGET` is
  set on a page that has no such hidden input.

### Clicking

`click="name"` finds a submit control inside the form — `input` of type
`submit` or `image`, or a `button` whose type is `submit` or absent — with that
`name`, and appends its `(name, value)` after the overrides. A name that
matches no such control raises `ValueError`: overriding a missing field is
normal, but pressing a missing button is almost certainly a typo, and posting
without it would fail quietly on the server side.

### Destination

- URL: the form's `action` resolved against the response's URL, the same way
  `urljoin()` resolves a link; an absent or empty `action` means the
  response's own URL.
- Method: the form's `method`, case-insensitive; `POST` sends the fields as
  `data`, anything else sends them as a `GET` with the fields as `params`.

### Errors

`ValueError` when no form matches (`form=None` on a page without any `<form>`,
or a selector matching nothing), and when `click` names no submit control.
Neither falls back to something else: a GET to the same page instead of a
post would look like a working crawl.

### Layout

- `collector/crawler/form.py` — new. A pure function

  ```python
  def form_request(
      page: Selector, base_url: str, *,
      form: str | None, formdata: Mapping[str, str | None] | None, click: str | None,
  ) -> tuple[str, str, list[tuple[str, str]]]:
  ```

  returning `(url, method, fields)`. No HTTP, no `Crawler`: it is tested on a
  bare HTML string.
- `Response.form_request()` calls it with `self.selector()` and
  `self.request.url`, then forwards to `self.crawler.request()` with the fields
  as `data` or `params` — the same delegation `follow()` uses, so a bare
  `callback=None` is resolved in one place.
- `Request.data` widens to `dict[str, str] | list[tuple[str, str]] | str | None`
  and `Request.params` to `dict[str, Any] | list[tuple[str, Any]] | None`;
  `Crawler.request()` and `Response.follow()` widen the same parameters to
  match. `curl_cffi` urlencodes a list of pairs as it does a dict, so the
  transport does not change.

## Testing

`tests/test_form.py`, no network, forms as HTML strings:

- collected controls: `disabled`, nameless, checkbox/radio checked and not,
  checkbox without `value`, select with and without `selected`, `select
  multiple`, option without `value`, textarea, buttons and submit inputs
  skipped, file input skipped;
- destination: relative `action`, empty and absent `action`, `method="get"`
  sending `params`, missing `method` meaning GET;
- overrides: in-place replacement, duplicate names collapsed, `None` removing,
  a new name appended after the form's own fields;
- `click`: an `input type=submit`, a `button` without `type`, the pair placed
  after overrides, an unknown name raising;
- `form=`: picking the second form by selector, no form raising;
- through `Response.form_request()` with the suite's `FakeHttp`: the default
  callback is `parse`, `metadata` and `headers` reach the `Request`, and the
  body goes out as `data` for POST.

Plus an example in `examples/` (a form round trip), covered by
`test_examples.py` like the rest, and a README and CHANGELOG entry.

## Consequence for the iTender crawlers

Outside this repository, but the reason for it:

```python
# pagination, instead of extract_initial_tokens + build_payload
yield response.form_request(
    formdata={'__EVENTTARGET': next_target, '__EVENTARGUMENT': ''},
    metadata={'page': num_page + 1},
)
# filter reset: the loop over selects stays; collecting hidden/text/button goes
yield response.form_request(formdata=resets, click=button_name, metadata={...})
```

Pagination now posts four fields; through `form_request()` it posts the whole
form, including the empty login inputs a browser would send too. WebForms
normally accepts that, but it has to be checked against a live site when
those crawlers migrate.
