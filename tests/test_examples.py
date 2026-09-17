"""The examples are documentation, and documentation rots.

Every one of them keeps its crawl behind ``if __name__ == '__main__'``, so
importing is free and reaches no socket — which makes it cheap to check that
they still use the API this package actually has. A rename or a removed
parameter breaks them here rather than in front of someone reading them.

What this does not check is that they scrape correctly; that needs the sites
they point at, and belongs to whoever runs them.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from collector import Parser

EXAMPLES = Path(__file__).parent.parent / 'examples'
NAMES = sorted(path.stem for path in EXAMPLES.glob('*.py'))


def test_there_are_examples_to_check():
    """A glob that silently matches nothing would make every test below vacuous."""
    assert NAMES


@pytest.mark.parametrize('name', NAMES)
def test_an_example_imports_and_declares_a_parser(name):
    module = importlib.import_module(f'examples.{name}')

    parsers = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, Parser) and value is not Parser
    ]
    assert parsers, f'{name} defines no Parser'
    assert all(getattr(parser, 'name', None) for parser in parsers)
    assert callable(module.main)


@pytest.mark.parametrize('name', NAMES)
def test_an_example_is_listed_in_its_readme(name):
    """A new example nobody can find is half an example."""
    assert f'`{name}.py`' in (EXAMPLES / 'README.md').read_text(encoding='utf-8')
