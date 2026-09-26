"""A crawler's declared parameters: what a run may set, typed, and checked up front."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import pytest
from tests.conftest import FakeHttp

from collector import Crawler, CrawlerContext
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


# ── on a crawler ─────────────────────────────────────────────────────────────


class Paged(Crawler):
    name = 'paged'
    params = Knobs()

    async def parse(self, response: Any):
        yield {}


class Small(Paged):
    name = 'small'
    params = replace(Paged.params, pages=5)


class Free(Crawler):
    name = 'free'

    async def parse(self, response: Any):
        yield {}


def build(crawler_cls: type[Crawler], **params: Any) -> Crawler:
    return crawler_cls(CrawlerContext(http=FakeHttp(), params=params))


def test_a_crawler_reads_the_runs_values_typed():
    assert build(Paged, pages='3', since='2026-06-01').params == Knobs(
        pages=3, since=date(2026, 6, 1)
    )


def test_the_declaration_is_not_changed_by_a_run():
    build(Paged, pages='3')
    assert Paged.params == Knobs()


def test_a_subclass_narrows_the_defaults_with_replace():
    assert build(Small).params == Knobs(pages=5)
    assert build(Small, label='x').params == Knobs(pages=5, label='x')


def test_a_bad_value_fails_when_the_crawler_is_built():
    with pytest.raises(ValueError, match='pages'):
        build(Paged, pages='lots')


def test_a_crawler_without_a_declaration_keeps_free_form_params():
    crawler = build(Free, anything='at all')
    assert crawler.params is None
    assert crawler.ctx.params == {'anything': 'at all'}


# ── declaring ────────────────────────────────────────────────────────────────


def _define(declared: Any) -> type[Crawler]:
    class Declared(Crawler):
        name = 'declared'
        params = declared

        async def parse(self, response: Any):
            yield {}

    return Declared


@pytest.mark.parametrize('declared', [{'pages': 1}, Knobs], ids=['dict', 'class'])
def test_params_must_be_a_dataclass_instance(declared):
    with pytest.raises(TypeError, match='dataclass instance'):
        _define(declared)


@dataclass(frozen=True)
class Listed:
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Either:
    size: int | str = 1


@dataclass(frozen=True)
class Reserved:
    concurrency: int = 1


@pytest.mark.parametrize(
    ('declared', 'message'),
    [(Listed(), 'tags'), (Either(), 'size'), (Reserved(), 'reserved')],
)
def test_a_field_a_run_could_never_set_is_refused_when_the_class_is_defined(declared, message):
    with pytest.raises(TypeError, match=message):
        _define(declared)
