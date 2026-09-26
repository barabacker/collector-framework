"""Submitting an HTML form the way a browser would: which fields go, and where.

A pure function over a parsed page, with no HTTP and no crawler, so the
browser's rules can be tested on a string. ``Response.form_request()`` is the
way a crawler reaches it.

Fields are ``(name, value)`` pairs, not a dict: a ``select multiple`` and a
repeated name are both legal HTML, and a dict would keep one value of each.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

from parsel import Selector

#: Input types a browser never sends as a field of their own. A submit button
#: goes only when it is the one pressed — see ``click``.
_UNSENT_INPUTS = frozenset({'submit', 'image', 'button', 'reset', 'file'})

#: Every control that can carry a value, in document order.
_CONTROLS = ".//*[self::input or self::select or self::textarea][@name != ''][not(@disabled)]"


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
    fields = _override(_collect(node), formdata or {})
    if click is not None:
        fields.extend(_button(node, click))

    action = (node.attrib.get('action') or '').strip()
    url = urljoin(base_url, action) if action else base_url
    method = 'POST' if (node.attrib.get('method') or '').strip().upper() == 'POST' else 'GET'
    if method == 'GET':
        # A browser replaces the action's query with the form's fields; left
        # in, it would be merged with them instead.
        url = urlunsplit(urlsplit(url)._replace(query=''))
    return url, method, fields


def _find(page: Selector, form: str | None) -> Selector:
    """The form to submit. Not finding one is an error, never a fallback.

    A GET to the same page instead of a post would look like a crawl that
    worked.
    """
    if form is None:
        matches = page.xpath('//form')
    else:
        matches = [match for match in page.css(form) if match.root.tag == 'form']
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
            # A browser drops one leading newline right after the opening tag;
            # lxml keeps it. Only one line break is ever dropped.
            text = control.xpath('string()').get() or ''
            if text.startswith('\r\n'):
                text = text[2:]
            elif text.startswith('\n'):
                text = text[1:]
            fields.append((name, text))
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
        (name, value)
        for name, value in formdata.items()
        if name not in placed and value is not None
    )
    return result


def _button(node: Selector, name: str) -> list[tuple[str, str]]:
    """The pairs a pressed submit button adds to the body.

    An image input sends where it was clicked, ``name.x`` and ``name.y``,
    instead of its name — and that is what ASP.NET looks for to tell an
    ImageButton was pressed.

    Pressing a button that is not there raises rather than posting without it:
    overriding a missing field is normal, a missing button is a typo, and the
    server would answer the unpressed form as if nothing were wrong.
    """
    xpath = './/input[@name=$name][not(@disabled)] | .//button[@name=$name][not(@disabled)]'
    for control in node.xpath(xpath, name=name):
        kind = (control.attrib.get('type') or '').strip().lower()
        if control.root.tag == 'input' and kind == 'image':
            return [(f'{name}.x', '0'), (f'{name}.y', '0')]
        if control.root.tag == 'input' and kind == 'submit':
            return [(name, control.attrib.get('value', ''))]
        if control.root.tag == 'button' and kind in ('', 'submit'):
            return [(name, control.attrib.get('value', ''))]
    raise ValueError(f'no submit button named {name!r} in this form')


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
