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
        '<input name="" value="4">'
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


def test_the_input_type_is_matched_case_insensitively():
    html = '<form><input type="CHECKBOX" name="c" checked></form>'
    assert fields(html) == [('c', 'on')]


def test_a_select_sends_its_selected_option():
    html = (
        '<form><select name="status">'
        '<option value="">Все</option>'
        '<option value="2" selected>Прием заявок</option>'
        '</select></form>'
    )
    assert fields(html) == [('status', '2')]


def test_a_select_with_nothing_selected_sends_its_first_option():
    html = (
        '<form><select name="s"><option value="1">a</option><option value="2">b</option></select>'
        '</form>'
    )
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


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('\nline one\nline two', 'line one\nline two'),
        ('\r\nx', 'x'),
        ('\n\nx', '\nx'),
    ],
)
def test_a_textarea_sends_its_text_with_only_the_one_leading_newline_dropped(text, expected):
    # A browser drops one newline right after <textarea>; lxml keeps it.
    html = f'<form><textarea name="t">{text}</textarea></form>'
    assert fields(html) == [('t', expected)]


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
    html = (
        '<form><input name="a" value="1"></form><form id="second"><input name="b" value="2"></form>'
    )
    assert fields(html, form='#second') == [('b', '2')]


def test_a_page_without_a_form_is_an_error():
    with pytest.raises(ValueError, match='no <form>'):
        submit('<p>nothing to submit</p>')


def test_a_selector_matching_nothing_is_an_error():
    with pytest.raises(ValueError, match='#missing'):
        submit('<form></form>', form='#missing')


def test_a_selector_matching_something_other_than_a_form_is_an_error():
    with pytest.raises(ValueError, match='#box'):
        submit('<div id="box"><form></form></div>', form='#box')
