# Response.form_request() Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `response.form_request(formdata=..., click=...)` builds the `Request` a browser would send when submitting an HTML form on the page.

**Architecture:** A pure function in a new module `collector/crawler/form.py` turns a parsel `Selector` plus a base URL into `(url, method, fields)`, where `fields` is a list of `(name, value)` pairs collected by the browser's rules for successful controls. `Response.form_request()` calls it and forwards to `crawler.request()`, the same delegation `Response.follow()` uses. `Request.data` / `Request.params` widen to accept a list of pairs.

**Tech Stack:** Python ≥ 3.11, parsel (lxml), curl_cffi, pytest (+ pytest-asyncio, `asyncio_mode = "auto"`), ruff (single quotes, line length 100), uv.

**Spec:** `docs/superpowers/specs/2026-09-26-form-request-design.md`

**Conventions in this repo** (follow them in every task):
- Single quotes; `from __future__ import annotations` at the top of every module.
- Test names are sentences: `test_a_select_sends_its_selected_option`.
- Docstrings and comments explain *why*, not what; match the density of the surrounding file.
- Run everything through `uv run`. Baseline before starting: `uv run pytest -q` → `170 passed, 15 deselected`.
- Commit messages: imperative, capitalised, no prefix (`Add …`, `Widen …`), ending with the line
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `src/collector/crawler/request.py` | modify | `data` / `params` accept a list of pairs |
| `src/collector/crawler/crawler.py` | modify | `Crawler.request()` signature widened to match |
| `src/collector/crawler/response.py` | modify | `follow()` widened; new `form_request()` method |
| `src/collector/crawler/form.py` | create | pure form → `(url, method, fields)` |
| `tests/test_request.py` | modify | list-of-pairs reaches the transport |
| `tests/test_form.py` | create | collection, destination, overrides, click |
| `tests/test_response.py` | modify | `Response.form_request()` delegation |
| `examples/forms.py` | create | runnable example: log in with a CSRF-carrying form |
| `examples/README.md` | modify | list the new example |
| `README.md`, `CHANGELOG.md` | modify | document it |

---

### Task 1: Widen `data` and `params` to accept a list of pairs

**Files:**
- Modify: `src/collector/crawler/request.py` (the `data` / `params` fields)
- Modify: `src/collector/crawler/crawler.py` (`Crawler.request()` parameters)
- Modify: `src/collector/crawler/response.py` (`Response.follow()` parameters)
- Test: `tests/test_request.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_request.py`:

```python
def test_a_list_of_pairs_reaches_the_transport_as_is():
    """A form can repeat a name — a dict would keep only the last value."""
    pairs = [('lot', '1'), ('lot', '2')]
    req = Request(url='https://example.test/', method='POST', data=pairs, params=[('p', 1)])

    assert req.http_kwargs() == {'params': [('p', 1)], 'data': pairs}
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_request.py -q`
Expected: PASS already — `http_kwargs()` passes values through untouched. This change is to the annotations; the test pins the behaviour they now promise.

- [ ] **Step 3: Widen the annotations**

In `src/collector/crawler/request.py`, replace the two field lines:

```python
    data: dict[str, str] | str | None = None
    params: dict[str, Any] | None = None
```

with:

```python
    #: A list of pairs, not only a dict: a form may send one name twice.
    data: dict[str, str] | list[tuple[str, str]] | str | None = None
    params: dict[str, Any] | list[tuple[str, Any]] | None = None
```

In `src/collector/crawler/crawler.py`, in `Crawler.request()`, replace:

```python
        data: dict[str, str] | str | None = None,
        params: dict[str, Any] | None = None,
```

with:

```python
        data: dict[str, str] | list[tuple[str, str]] | str | None = None,
        params: dict[str, Any] | list[tuple[str, Any]] | None = None,
```

In `src/collector/crawler/response.py`, in `Response.follow()`, make the same replacement of the `data` and `params` parameter lines.

- [ ] **Step 4: Run the suite and lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: `171 passed, 15 deselected`; ruff reports no issues.

- [ ] **Step 5: Commit**

```bash
git add src/collector/crawler/request.py src/collector/crawler/crawler.py src/collector/crawler/response.py tests/test_request.py
git commit -m "Accept a list of pairs for a request's data and params

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Collect a form's fields and work out where it goes

**Files:**
- Create: `src/collector/crawler/form.py`
- Test: `tests/test_form.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_form.py`:

```python
"""Which fields a form sends, and where — the browser's rules, without a browser."""

from __future__ import annotations

from typing import Any

import pytest
from parsel import Selector

from collector.crawler.form import form_request

BASE = 'https://example.test/list/'


def submit(html: str, **kwargs: Any) -> tuple[str, str, list[tuple[str, str]]]:
    return form_request(Selector(text=html), BASE, **kwargs)


def fields(html: str, **kwargs: Any) -> list[tuple[str, str]]:
    return submit(html, **kwargs)[2]


# ── what is collected ────────────────────────────────────────────────────────


def test_hidden_text_and_untyped_inputs_are_sent_with_their_value():
    html = (
        '<form>'
        '<input type="hidden" name="__VIEWSTATE" value="abc">'
        '<input type="text" name="q" value="x">'
        '<input name="plain" value="y">'
        '<input type="password" name="pw">'
        '</form>'
    )
    assert fields(html) == [('__VIEWSTATE', 'abc'), ('q', 'x'), ('plain', 'y'), ('pw', '')]


def test_disabled_and_nameless_controls_are_not_sent():
    html = (
        '<form>'
        '<input name="a" value="1" disabled>'
        '<input value="2">'
        '<select name="s" disabled><option>x</option></select>'
        '<input name="b" value="3">'
        '</form>'
    )
    assert fields(html) == [('b', '3')]


def test_buttons_and_file_inputs_are_not_sent_unless_clicked():
    html = (
        '<form>'
        '<input type="submit" name="go" value="Искать">'
        '<input type="image" name="img" src="x.png">'
        '<input type="button" name="btn" value="b">'
        '<input type="reset" name="rst">'
        '<input type="file" name="upload">'
        '<button name="press" value="1">Press</button>'
        '<input name="k" value="v">'
        '</form>'
    )
    assert fields(html) == [('k', 'v')]


def test_checkbox_and_radio_are_sent_only_when_checked():
    html = (
        '<form>'
        '<input type="checkbox" name="c1" value="yes" checked>'
        '<input type="checkbox" name="c2" value="no">'
        '<input type="checkbox" name="c3" checked>'
        '<input type="radio" name="r" value="a">'
        '<input type="radio" name="r" value="b" checked>'
        '</form>'
    )
    # A checkbox with no value is sent as "on", as a browser does.
    assert fields(html) == [('c1', 'yes'), ('c3', 'on'), ('r', 'b')]


def test_a_select_sends_its_selected_option():
    html = (
        '<form><select name="status">'
        '<option value="">Все</option>'
        '<option value="2" selected>Прием заявок</option>'
        '</select></form>'
    )
    assert fields(html) == [('status', '2')]


def test_a_select_with_nothing_selected_sends_its_first_option():
    html = '<form><select name="s"><option value="1">a</option><option value="2">b</option></select></form>'
    assert fields(html) == [('s', '1')]


def test_an_option_without_a_value_sends_its_normalised_text():
    html = '<form><select name="s"><option selected>  Прием \n заявок </option></select></form>'
    assert fields(html) == [('s', 'Прием заявок')]


def test_a_multiple_select_sends_every_selected_option_and_nothing_by_default():
    html = (
        '<form>'
        '<select name="m" multiple>'
        '<option value="1" selected>a</option><option value="2">b</option>'
        '<option value="3" selected>c</option>'
        '</select>'
        '<select name="none" multiple><option value="1">a</option></select>'
        '</form>'
    )
    assert fields(html) == [('m', '1'), ('m', '3')]


def test_a_textarea_sends_its_text_without_the_leading_newline():
    # A browser drops one newline right after <textarea>; lxml keeps it.
    html = '<form><textarea name="t">\nline one\nline two</textarea></form>'
    assert fields(html) == [('t', 'line one\nline two')]


def test_fields_keep_document_order():
    html = (
        '<form>'
        '<textarea name="t">x</textarea>'
        '<select name="s"><option value="1">a</option></select>'
        '<input name="i" value="2">'
        '</form>'
    )
    assert [name for name, _ in fields(html)] == ['t', 's', 'i']


# ── where it goes ────────────────────────────────────────────────────────────


def test_a_relative_action_resolves_against_the_page():
    url, method, _ = submit('<form action="search?x=1" method="post"></form>')
    assert (url, method) == ('https://example.test/list/search?x=1', 'POST')


@pytest.mark.parametrize('form', ['<form action=""></form>', '<form></form>'])
def test_an_empty_or_absent_action_posts_back_to_the_page(form):
    assert submit(form)[0] == BASE


@pytest.mark.parametrize(
    ('attr', 'expected'),
    [('method="Post"', 'POST'), ('method="get"', 'GET'), ('', 'GET'), ('method="dialog"', 'GET')],
)
def test_the_method_comes_from_the_form_and_defaults_to_get(attr, expected):
    assert submit(f'<form {attr}></form>')[1] == expected


# ── which form ───────────────────────────────────────────────────────────────


def test_the_first_form_is_used_by_default():
    html = '<form><input name="a" value="1"></form><form><input name="b" value="2"></form>'
    assert fields(html) == [('a', '1')]


def test_a_selector_picks_the_form():
    html = '<form><input name="a" value="1"></form><form id="second"><input name="b" value="2"></form>'
    assert fields(html, form='#second') == [('b', '2')]


def test_a_page_without_a_form_is_an_error():
    with pytest.raises(ValueError, match='no <form>'):
        submit('<p>nothing to submit</p>')


def test_a_selector_matching_nothing_is_an_error():
    with pytest.raises(ValueError, match='#missing'):
        submit('<form></form>', form='#missing')
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_form.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'collector.crawler.form'`.

- [ ] **Step 3: Implement**

Create `src/collector/crawler/form.py`:

```python
"""Submitting an HTML form the way a browser would: which fields go, and where.

A pure function over a parsed page, with no HTTP and no crawler, so the
browser's rules can be tested on a string. ``Response.form_request()`` is the
way a crawler reaches it.

Fields are ``(name, value)`` pairs, not a dict: a ``select multiple`` and a
repeated name are both legal HTML, and a dict would keep one value of each.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urljoin

from parsel import Selector

#: Input types a browser never sends as a field of their own. A submit button
#: goes only when it is the one pressed — see ``click``.
_UNSENT_INPUTS = frozenset({'submit', 'image', 'button', 'reset', 'file'})

#: Every control that can carry a value, in document order.
_CONTROLS = './/*[self::input or self::select or self::textarea][@name][not(@disabled)]'


def form_request(
    page: Selector,
    base_url: str,
    *,
    form: str | None = None,
    formdata: Mapping[str, str | None] | None = None,
    click: str | None = None,
) -> tuple[str, str, list[tuple[str, str]]]:
    """``(url, method, fields)`` a browser would submit for a form on ``page``.

    ``form`` is a CSS selector, ``None`` meaning the first form on the page.
    ``formdata`` overrides the collected fields and ``click`` names the submit
    button to press; without it no button is sent at all.
    """
    node = _find(page, form)
    fields = _collect(node)

    action = (node.attrib.get('action') or '').strip()
    url = urljoin(base_url, action) if action else base_url
    method = 'POST' if (node.attrib.get('method') or '').strip().upper() == 'POST' else 'GET'
    return url, method, fields


def _find(page: Selector, form: str | None) -> Selector:
    """The form to submit. Not finding one is an error, never a fallback.

    A GET to the same page instead of a post would look like a crawl that
    worked.
    """
    matches = page.xpath('//form') if form is None else page.css(form)
    if not matches:
        raise ValueError('no <form> on this page' if form is None else f'no form matches {form!r}')
    return matches[0]


def _collect(node: Selector) -> list[tuple[str, str]]:
    """The successful controls of a form, as a browser would send them."""
    fields: list[tuple[str, str]] = []
    for control in node.xpath(_CONTROLS):
        name = control.attrib['name']
        tag = control.root.tag
        if tag == 'select':
            fields.extend((name, value) for value in _selected(control))
        elif tag == 'textarea':
            # A browser drops one newline right after the opening tag; lxml keeps it.
            text = control.xpath('string()').get() or ''
            fields.append((name, text.removeprefix('\r\n').removeprefix('\n')))
        else:
            kind = (control.attrib.get('type') or 'text').strip().lower()
            if kind in _UNSENT_INPUTS:
                continue
            if kind in ('checkbox', 'radio'):
                if 'checked' in control.attrib:
                    fields.append((name, control.attrib.get('value', 'on')))
                continue
            fields.append((name, control.attrib.get('value', '')))
    return fields


def _selected(select: Selector) -> list[str]:
    """Values a select sends: its selected options, or the first one when none is.

    The fallback is a single select's: a ``multiple`` one with nothing selected
    sends nothing.
    """
    options = select.xpath('.//option')
    chosen = [option for option in options if 'selected' in option.attrib]
    if not chosen and 'multiple' not in select.attrib:
        chosen = options[:1]
    return [_option_value(option) for option in chosen]


def _option_value(option: Selector) -> str:
    """An option's ``value``, or its whitespace-normalised text without one."""
    if 'value' in option.attrib:
        return option.attrib['value']
    return ' '.join((option.xpath('string()').get() or '').split())
```

Note: `formdata` and `click` are accepted but not yet applied — Tasks 3 and 4 wire them in.

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_form.py -q`
Expected: all pass (21 tests, counting parametrised cases).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .` — expected clean.

```bash
git add src/collector/crawler/form.py tests/test_form.py
git commit -m "Collect a form's fields by the browser's rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Apply `formdata` overrides

**Files:**
- Modify: `src/collector/crawler/form.py`
- Test: `tests/test_form.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_form.py`:

```python
# ── overrides ────────────────────────────────────────────────────────────────

PAGER = (
    '<form method="post">'
    '<input type="hidden" name="__EVENTTARGET" value="">'
    '<input type="hidden" name="__VIEWSTATE" value="abc">'
    '<input name="q" value="old">'
    '</form>'
)


def test_an_override_replaces_the_value_in_place():
    assert fields(PAGER, formdata={'__EVENTTARGET': 'pager$2'}) == [
        ('__EVENTTARGET', 'pager$2'),
        ('__VIEWSTATE', 'abc'),
        ('q', 'old'),
    ]


def test_an_override_collapses_a_repeated_name_to_one_value():
    html = '<form><select name="m" multiple><option selected>a</option><option selected>b</option></select></form>'
    assert fields(html, formdata={'m': 'c'}) == [('m', 'c')]


def test_none_removes_the_field():
    assert fields(PAGER, formdata={'q': None}) == [('__EVENTTARGET', ''), ('__VIEWSTATE', 'abc')]


def test_a_name_the_form_lacks_is_appended():
    assert fields('<form><input name="a" value="1"></form>', formdata={'__EVENTARGUMENT': ''}) == [
        ('a', '1'),
        ('__EVENTARGUMENT', ''),
    ]


def test_none_for_a_name_the_form_lacks_changes_nothing():
    assert fields('<form><input name="a" value="1"></form>', formdata={'b': None}) == [('a', '1')]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_form.py -q -k "override or none or lacks"`
Expected: FAIL — `formdata` is ignored, so e.g. `('__EVENTTARGET', '')` where `('__EVENTTARGET', 'pager$2')` was expected.

- [ ] **Step 3: Implement**

In `src/collector/crawler/form.py`, in `form_request()`, replace:

```python
    fields = _collect(node)
```

with:

```python
    fields = _override(_collect(node), formdata or {})
```

and add this function after `_collect()`:

```python
def _override(
    fields: list[tuple[str, str]], formdata: Mapping[str, str | None]
) -> list[tuple[str, str]]:
    """Apply ``formdata`` over the collected fields.

    A name the form has keeps its place — the first one, if it repeats — so
    the body reads in the order a browser would send it. ``None`` removes the
    name. A name the form lacks is appended: that is how ``__EVENTTARGET`` gets
    set on a page that renders no such input.
    """
    result: list[tuple[str, str]] = []
    placed: set[str] = set()
    for name, value in fields:
        if name not in formdata:
            result.append((name, value))
            continue
        if name in placed:
            continue
        placed.add(name)
        if (override := formdata[name]) is not None:
            result.append((name, override))
    result.extend(
        (name, value) for name, value in formdata.items() if name not in placed and value is not None
    )
    return result
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_form.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .` — expected clean.

```bash
git add src/collector/crawler/form.py tests/test_form.py
git commit -m "Override a form's fields with formdata

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Press a submit button with `click`

**Files:**
- Modify: `src/collector/crawler/form.py`
- Test: `tests/test_form.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_form.py`:

```python
# ── click ────────────────────────────────────────────────────────────────────

SEARCH = (
    '<form method="post">'
    '<input type="submit" name="login" value="Войти">'
    '<input name="q" value="лот">'
    '<input type="submit" name="search" value="Искать">'
    '<button name="clear" value="1">Очистить</button>'
    '<button type="button" name="toggle">…</button>'
    '</form>'
)


def test_nothing_is_clicked_by_default():
    assert fields(SEARCH) == [('q', 'лот')]


def test_a_clicked_submit_input_is_appended_after_the_overrides():
    assert fields(SEARCH, formdata={'extra': 'x'}, click='search') == [
        ('q', 'лот'),
        ('extra', 'x'),
        ('search', 'Искать'),
    ]


def test_a_button_without_a_type_is_a_submit_button():
    assert fields(SEARCH, click='clear') == [('q', 'лот'), ('clear', '1')]


@pytest.mark.parametrize('name', ['missing', 'toggle', 'q'])
def test_clicking_something_that_is_not_a_submit_button_is_an_error(name):
    with pytest.raises(ValueError, match=name):
        fields(SEARCH, click=name)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_form.py -q -k click`
Expected: FAIL — `click` is ignored, so no pair is appended and no `ValueError` is raised.

- [ ] **Step 3: Implement**

In `src/collector/crawler/form.py`, in `form_request()`, after the line

```python
    fields = _override(_collect(node), formdata or {})
```

add:

```python
    if click is not None:
        fields.append(_button(node, click))
```

and add this function after `_override()`:

```python
def _button(node: Selector, name: str) -> tuple[str, str]:
    """The pair a pressed submit button adds to the body.

    Pressing a button that is not there raises rather than posting without it:
    overriding a missing field is normal, a missing button is a typo, and the
    server would answer the unpressed form as if nothing were wrong.
    """
    for control in node.xpath('.//input[@name=$name] | .//button[@name=$name]', name=name):
        kind = (control.attrib.get('type') or '').strip().lower()
        if control.root.tag == 'input' and kind in ('submit', 'image'):
            return name, control.attrib.get('value', '')
        if control.root.tag == 'button' and kind in ('', 'submit'):
            return name, control.attrib.get('value', '')
    raise ValueError(f'no submit button named {name!r} in this form')
```

- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_form.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .` — expected clean.

```bash
git add src/collector/crawler/form.py tests/test_form.py
git commit -m "Press a form's submit button only when it is named

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `Response.form_request()`

**Files:**
- Modify: `src/collector/crawler/response.py`
- Test: `tests/test_response.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_response.py`:

```python
FORM_PAGE = (
    '<form method="post" action="/list/">'
    '<input type="hidden" name="__VIEWSTATE" value="abc">'
    '<input type="submit" name="go" value="Искать">'
    '</form>'
)


def test_form_request_posts_the_form_through_the_crawler():
    crawler = _crawler()
    response = Response(
        FakeResponse(text=FORM_PAGE), Request(url='https://example.test/list/?page=1'), crawler
    )

    req = response.form_request(
        formdata={'__EVENTTARGET': 'pager$2'},
        click='go',
        metadata={'page': 2},
        headers={'Referer': 'https://example.test/list/'},
    )

    assert (req.method, req.url) == ('POST', 'https://example.test/list/')
    assert req.http_kwargs() == {
        'headers': {'Referer': 'https://example.test/list/'},
        'data': [('__VIEWSTATE', 'abc'), ('__EVENTTARGET', 'pager$2'), ('go', 'Искать')],
    }
    assert req.metadata == {'page': 2}
    # Built by crawler.request(), so a bare callback means parse() — as with follow().
    assert req.callback == crawler.parse


def test_a_get_form_sends_its_fields_as_params():
    crawler = _crawler()
    page = FakeResponse(text='<form action="/search"><input name="q" value="лот"></form>')
    response = Response(page, Request(url='https://example.test/list/'), crawler)

    req = response.form_request(callback=crawler.parse)

    assert (req.method, req.url) == ('GET', 'https://example.test/search')
    assert req.http_kwargs() == {'params': [('q', 'лот')]}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_response.py -q -k form`
Expected: FAIL — `AttributeError: 'Response' object has no attribute 'form_request'`.

- [ ] **Step 3: Implement**

In `src/collector/crawler/response.py`:

1. Add to the standard-library imports at the top (after `import json as jsonlib`):

```python
from collections.abc import Mapping
```

and, directly above `from collector.crawler.request import Request`:

```python
from collector.crawler.form import form_request as _form_request
```

(The alias keeps the module function from being confused with the method of the same name.)

2. Add this method at the end of the `Response` class, after `follow()`:

```python
    def form_request(
        self,
        *,
        form: str | None = None,
        formdata: Mapping[str, str | None] | None = None,
        click: str | None = None,
        callback: Any = None,
        metadata: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Request:
        """Build the ``Request`` a browser would send submitting a form on this page.

        The fields are the form's own, collected by the browser's rules, with
        ``formdata`` over them: a value replaces the field, ``None`` removes it,
        and a name the form lacks is added. ``click`` names the submit button
        to press; left out, none is — a WebForms page is one form around the
        whole page, and its first button is as likely to be "log in" as
        "search". ``form`` is a CSS selector, the first form on the page if
        left out. See :mod:`collector.crawler.form` for the rules.

        Forwards to ``crawler.request()``, as ``follow()`` does.
        """
        url, method, fields = _form_request(
            self.selector(), self.request.url, form=form, formdata=formdata, click=click
        )
        # A GET form sends its fields in the query string, as a browser does.
        body = {'data': fields} if method == 'POST' else {'params': fields}
        return self.crawler.request(
            url, method=method, callback=callback, metadata=metadata, headers=headers, **body
        )
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: everything passes (baseline 171 + the new form and response tests), 15 deselected.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .` — expected clean. If ruff's isort moves the new imports, run `uv run ruff check --fix .` and re-run the suite.

```bash
git add src/collector/crawler/response.py tests/test_response.py
git commit -m "Add Response.form_request(): submit a form on the page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Example and documentation

**Files:**
- Create: `examples/forms.py`
- Modify: `examples/README.md`
- Modify: `README.md` (the "Response helpers" bullet, around line 78)
- Modify: `CHANGELOG.md` (`## [Unreleased]` → `### Added` and `### Changed`)

- [ ] **Step 1: Write the example**

Create `examples/forms.py`:

```python
"""A form: log in by posting the page's own form back, CSRF token and all.

The login page on the sandbox carries a hidden ``csrf_token`` that has to go
back with the credentials. ``form_request()`` collects every field the form
would send — the token included, without this crawler naming it — and lays
``formdata`` over them. No submit button is pressed unless ``click=`` names one.

The sandbox accepts any username and password.

    uv run python examples/forms.py
"""

from __future__ import annotations

import sys
from typing import Any

from collector import Crawler, Response, Settings, collect


class Login(Crawler):
    name = 'login'
    start_urls = ['https://quotes.toscrape.com/login']
    settings = Settings(delay=0.3, max_requests=3)

    async def parse(self, response: Response) -> Any:
        yield response.form_request(
            formdata={'username': 'demo', 'password': 'demo'},
            callback=self.after_login,
        )

    async def after_login(self, response: Response) -> Any:
        page = response.selector()
        yield {
            # Only a logged-in page links to /logout.
            'logged_in': bool(page.css('a[href="/logout"]')),
            'first_quote': page.css('div.quote span.text::text').get(),
        }


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    for item in collect(Login):
        print(f'logged in: {item["logged_in"]}')
        print(f'  first quote: {item["first_quote"]}')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run the examples test to see it fail**

Run: `uv run pytest tests/test_examples.py -q`
Expected: FAIL on `test_an_example_is_listed_in_its_readme[forms]`.

- [ ] **Step 3: List it in `examples/README.md`**

Change `Seven runnable scripts` to `Eight runnable scripts`. Add this row after the `json_api.py` row:

```markdown
| [`forms.py`](forms.py) | `form_request()`: post a page's own form back — hidden CSRF token included — with a few fields overridden. |
```

In the "They use the network" paragraph, change
`` `quotes.py`, `json_api.py`, `streaming.py` and `pipeline.py` crawl `` to
`` `quotes.py`, `json_api.py`, `forms.py`, `streaming.py` and `pipeline.py` crawl ``.

- [ ] **Step 4: Run the examples test to see it pass**

Run: `uv run pytest tests/test_examples.py -q`
Expected: all pass.

- [ ] **Step 5: README and CHANGELOG**

In `README.md`, replace the "Response helpers" bullet:

```markdown
- **Response helpers** — `status`, `text`, `headers`, `json()`, `urljoin()`,
  `follow()` for a link on the page, and `selector()` (parsel), which parses the
```

(keep the rest of that bullet as it is) so that it starts:

```markdown
- **Response helpers** — `status`, `text`, `headers`, `json()`, `urljoin()`,
  `follow()` for a link on the page, `form_request()` to submit a form on it
  the way a browser would (hidden fields included, nothing clicked unless
  named), and `selector()` (parsel), which parses the
```

In `CHANGELOG.md`, add as the first bullet under `## [Unreleased]` → `### Added`:

```markdown
- `Response.form_request(form=, formdata=, click=)` — the `Request` a browser
  would send submitting a form on the page: its successful controls collected
  by the browser's rules (hidden and text inputs, checked boxes, selected
  options, textareas; nothing disabled, no buttons), `formdata` laid over them
  (a value replaces a field in place, `None` removes it, a new name is
  appended), and a submit button only when `click` names one. The URL and
  method come from the form's `action` and `method`. A crawler submitting an
  ASP.NET WebForms postback or a form with a CSRF token no longer rebuilds the
  body by hand. The rules live in `collector.crawler.form` as a pure function.
```

and as the first bullet under `### Changed`:

```markdown
- `Request.data` and `Request.params` — and the same parameters on
  `Crawler.request()` and `Response.follow()` — accept a list of `(name, value)`
  pairs as well as a dict. A form may send one name twice (a `select multiple`),
  which a dict cannot hold; `curl_cffi` already encoded a list of pairs the same
  way, so only the annotations change.
```

- [ ] **Step 6: Full verification and commit**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all pass, 15 deselected; ruff clean.

```bash
git add examples/forms.py examples/README.md README.md CHANGELOG.md
git commit -m "Document form_request(), with an example that logs in

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Run the example against the sandbox (network)**

Run: `uv run python examples/forms.py`
Expected output starts with `logged in: True`. If it prints `False`, the form round trip is wrong — inspect what was sent before changing anything.
