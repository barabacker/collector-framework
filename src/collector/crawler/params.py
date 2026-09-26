"""Parameters a crawler declares for a run, and turning a run's values into them.

A crawler sets ``params`` to an instance of a frozen dataclass: its fields are
what a run may set, their defaults are the crawler's own, and a subclass
narrows them with ``dataclasses.replace`` — the way it narrows ``settings``. A
run's values arrive as strings (a CLI flag, a job payload) or already typed,
and become a new instance; the declared one is never changed, so two runs of
one crawler cannot see each other's values.

Where ``engine.params`` forgives a bad value for the engine's two knobs, this
refuses one: a date window that silently fell back to "no window" would crawl
everything, and nobody would notice until the bill came.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

#: Keys the engine reads out of the same mapping. Always accepted, and never a
#: crawler's field — the two would silently share one value.
ENGINE_KEYS = frozenset({'concurrency', 'max_requests'})

_TRUE = frozenset({'true', '1', 'yes'})
_FALSE = frozenset({'false', '0', 'no'})


def _parse_bool(value: str) -> bool:
    word = value.strip().lower()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    raise ValueError(value)


#: How a string becomes each supported type. ``str`` needs nothing.
_FROM_STRING: dict[type, Callable[[str], Any]] = {
    str: str,
    int: int,
    float: float,
    bool: _parse_bool,
    date: date.fromisoformat,
    datetime: datetime.fromisoformat,
}


def _accepts(kind: type, value: Any) -> bool:
    """Whether an already-typed value is one of ``kind``.

    ``isinstance`` alone is too generous twice over: ``True`` is an ``int``,
    and a ``datetime`` is a ``date`` whose time would be dropped unnoticed.
    """
    if isinstance(value, bool):
        return kind is bool
    if kind is float:
        return isinstance(value, int | float)
    if kind is date:
        return isinstance(value, date) and not isinstance(value, datetime)
    return isinstance(value, kind)


def _unwrap(kind: Any) -> tuple[type, bool]:
    """``(base type, optional)`` of a field's annotation, or ``TypeError``."""
    optional = False
    if typing.get_origin(kind) in (typing.Union, types.UnionType):
        members = typing.get_args(kind)
        rest = [member for member in members if member is not type(None)]
        if len(rest) != 1 or len(rest) == len(members):
            raise TypeError(f'unsupported type {kind!r}')
        kind, optional = rest[0], True
    if kind not in _FROM_STRING:
        raise TypeError(f'unsupported type {kind!r}')
    return kind, optional


def _field_types(declared: Any) -> dict[str, Any]:
    """Each settable field's annotation, resolved even under postponed annotations."""
    hints = typing.get_type_hints(type(declared))
    return {field.name: hints[field.name] for field in dataclasses.fields(declared) if field.init}


def _convert(name: str, annotation: Any, value: Any) -> Any:
    kind, optional = _unwrap(annotation)
    if value is None or (optional and value == ''):
        if optional:
            return None
    elif isinstance(value, str):
        try:
            return _FROM_STRING[kind](value)
        except ValueError:
            pass
    elif _accepts(kind, value):
        return float(value) if kind is float else value
    raise ValueError(f'params[{name!r}]: expected {kind.__name__}, got {value!r}')


def check_declaration(crawler_cls: type) -> None:
    """Refuse a ``params`` declaration a run could never fill.

    Run when the crawler class is defined, so the mistake surfaces on import
    rather than on the first run that happens to set the field.
    """
    declared = getattr(crawler_cls, 'params', None)
    if declared is None:
        return
    owner = crawler_cls.__name__
    if isinstance(declared, type) or not dataclasses.is_dataclass(declared):
        raise TypeError(f'{owner}.params must be a dataclass instance or None, got {declared!r}')
    for name, annotation in _field_types(declared).items():
        if name in ENGINE_KEYS:
            raise TypeError(f'{owner}.params.{name}: the name is reserved for the engine')
        try:
            _unwrap(annotation)
        except TypeError as exc:
            raise TypeError(f'{owner}.params.{name}: {exc}') from None


def resolve_params(declared: Any, raw: Mapping[str, Any]) -> Any:
    """The declared parameters with a run's values applied, converted and checked.

    ``None`` declared means the crawler takes free-form params: nothing is
    checked and ``None`` comes back. Otherwise every key must be a field or one
    of the engine's knobs, and every value must convert to its field's type.
    """
    if declared is None:
        return None
    annotations = _field_types(declared)
    unknown = sorted(set(raw) - annotations.keys() - ENGINE_KEYS)
    if unknown:
        known = ', '.join(sorted(annotations.keys() | ENGINE_KEYS))
        raise ValueError(f'unknown param {", ".join(map(repr, unknown))}; known: {known}')
    overrides = {
        name: _convert(name, annotations[name], value)
        for name, value in raw.items()
        if name in annotations
    }
    return dataclasses.replace(declared, **overrides)
