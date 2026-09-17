"""The package's own metadata: one version, not four that drift apart."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import collector

PYPROJECT = Path(__file__).parent.parent / 'pyproject.toml'
README = Path(__file__).parent.parent / 'README.md'
CHANGELOG = Path(__file__).parent.parent / 'CHANGELOG.md'


def _declared() -> str:
    return tomllib.loads(PYPROJECT.read_text())['project']['version']


def test_dunder_version_matches_the_distribution():
    assert collector.__version__ == _declared() == version('collector-framework')


def test_the_readme_status_names_the_current_version():
    assert f'`{_declared()}`' in README.read_text()


def test_the_changelog_leads_with_the_current_version():
    """The newest released section names the shipped version.

    An ``[Unreleased]`` section above it is Keep a Changelog's own convention
    for work in flight, so it is skipped rather than treated as a version.
    """
    headings = re.findall(r'^## \[(.+?)\]', CHANGELOG.read_text(), re.MULTILINE)
    released = [h for h in headings if h.lower() != 'unreleased']
    assert released and released[0] == _declared()


def test_the_root_exports_what_a_parser_author_writes():
    """The transport lives in collector.http; growing this list back is a decision."""
    assert set(collector.__all__) == {
        '__version__',
        'Parser',
        'ParserContext',
        'Request',
        'Response',
        'Settings',
        'RetryPolicy',
        'DEFAULT_RETRY_STATUSES',
        'Crawler',
        'Stats',
        'open_crawler',
        'crawl',
        'run_parser',
        'collect',
    }
