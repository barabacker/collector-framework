"""A crawler's declared parameters: what a run may set, typed, and checked up front."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from collector.crawler.params import resolve_params


@dataclass(frozen=True)
class Knobs:
    pages: int = 100
    since: date | None = None
    ratio: float = 1.0
    strict: bool = False
    label: str = 'all'
    at: datetime | None = None


# ── converting ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('name', 'raw', 'expected'),
    [
        ('pages', '5', 5),
        ('ratio', '0.5', 0.5),
        ('strict', 'YES', True),
        ('strict', 'true', True),
        ('strict', '0', False),
        ('strict', 'no', False),
        ('label', 'x', 'x'),
        ('since', '2026-06-01', date(2026, 6, 1)),
        ('since', '', None),
        ('at', '2026-06-01T10:00:00', datetime(2026, 6, 1, 10)),
    ],
)
def test_a_string_is_converted_to_the_fields_type(name, raw, expected):
    assert getattr(resolve_params(Knobs(), {name: raw}), name) == expected


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('pages', 5),
        ('strict', False),
        ('since', date(2026, 6, 1)),
        ('since', None),
        ('at', datetime(2026, 6, 1, 10)),
    ],
)
def test_an_already_typed_value_passes_through(name, value):
    assert getattr(resolve_params(Knobs(), {name: value}), name) == value


def test_an_int_for_a_float_field_is_stored_as_a_float():
    ratio = resolve_params(Knobs(), {'ratio': 2}).ratio
    assert (ratio, type(ratio)) == (2.0, float)


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('pages', '2O'),
        ('pages', ''),
        ('pages', None),
        ('pages', True),  # a bool is an int to Python, not to a crawler
        ('pages', 2.5),
        ('ratio', True),
        ('strict', 'maybe'),
        ('strict', 1),
        ('since', '2026-13-01'),
        ('since', datetime(2026, 6, 1)),  # the time would be silently dropped
        ('label', 5),
    ],
)
def test_a_bad_value_is_an_error(name, value):
    with pytest.raises(ValueError, match=name):
        resolve_params(Knobs(), {name: value})


def test_the_error_names_the_param_the_type_and_the_value():
    with pytest.raises(ValueError, match=r"params\['since'\]: expected date, got '2026-13-01'"):
        resolve_params(Knobs(), {'since': '2026-13-01'})


# ── which keys ───────────────────────────────────────────────────────────────


def test_a_field_the_run_does_not_set_keeps_the_declared_default():
    assert resolve_params(Knobs(pages=7), {'label': 'x'}) == Knobs(pages=7, label='x')


def test_the_engines_own_knobs_are_accepted_alongside_the_fields():
    raw = {'concurrency': '2', 'max_requests': '3', 'pages': '1'}
    assert resolve_params(Knobs(), raw) == Knobs(pages=1)


def test_an_unknown_key_is_an_error_listing_the_known_ones():
    known = 'at, concurrency, label, max_requests, pages, ratio, since, strict'
    with pytest.raises(ValueError, match=f"unknown param 'page'; known: {known}"):
        resolve_params(Knobs(), {'page': '1'})


def test_nothing_declared_means_nothing_is_checked():
    raw: dict[str, Any] = {'anything': 'at all'}
    assert resolve_params(None, raw) is None
