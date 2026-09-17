"""clean() collapses scraped whitespace and reports emptiness as None."""

from __future__ import annotations

from collector import clean


def test_collapses_whitespace_runs():
    assert clean('  Лот\n\t 12  ') == 'Лот 12'


def test_empty_becomes_none():
    assert clean('   \n ') is None
    assert clean('') is None


def test_none_passes_through():
    assert clean(None) is None
