"""Comparing the rows a generated query returned with the rows the gold query returns."""

from __future__ import annotations

from collections import Counter
from typing import Any

Rows = list[list[Any]]


def _norm(v: Any) -> str:
    return f"{v:.6g}" if isinstance(v, float) else repr(v)


def _columns(rows: Rows) -> list[Counter]:
    return [Counter(map(_norm, col)) for col in zip(*rows)] if rows else []


def exact(expected: Rows, actual: Rows) -> bool:
    """Same rows, any order (column order matters)."""
    return sorted(map(_norm_row, expected)) == sorted(map(_norm_row, actual))


def _norm_row(row: list[Any]) -> str:
    return "|".join(map(_norm, row))


def _found(want: Counter, have: list[Counter]) -> bool:
    return any(want == col for col in have)


def complete(expected: Rows, actual: Rows) -> bool:
    """Same number of rows, and every expected column's values appear as some actual column.

    Forgives extra columns, column order and aliases; does not forgive wrong rows, a wrong count or a missing column.
    """
    if len(expected) != len(actual):
        return False
    have = _columns(actual)
    return all(_found(want, have) for want in _columns(expected))


def entities(expected: Rows, actual: Rows) -> bool:
    """Same number of rows, and the expected *first* column (the thing asked about: the people, the departments,
    the count) appears as some actual column.

    The gold queries are deliberately verbose (name, email, department, ...). A leaner query that lists the
    right people is still a right answer, so only the leading column has to match. A wrong count of rows,
    or the wrong people, still fails.
    """
    if len(expected) != len(actual):
        return False
    if not expected:
        return True
    return _found(_columns(expected)[0], _columns(actual))


def grade(expected: Rows, actual: Rows) -> str:
    """exact > complete > entities > wrong"""
    if exact(expected, actual):
        return "exact"
    if complete(expected, actual):
        return "complete"
    if entities(expected, actual):
        return "entities"
    return "wrong"
