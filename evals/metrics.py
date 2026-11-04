"""Comparing the rows a generated query returned with the rows the gold query returns."""

from __future__ import annotations

from collections import Counter
from typing import Any

Rows = list[list[Any]]


def _norm(v: Any) -> str:
    """Canonical text for one cell so equal values compare equal.

    Floats are rounded to 2 decimals (so ``3.8889`` equals a reference that rounded to ``3.89``); everything else
    goes through ``repr``.
    """
    return f"{v:.2f}" if isinstance(v, float) else repr(v)


def _columns(rows: Rows) -> list[Counter]:
    """Each column of `rows` as a multiset of its normalized values (order-independent)."""
    return [Counter(map(_norm, col)) for col in zip(*rows)] if rows else []


def exact(expected: Rows, actual: Rows) -> bool:
    """Same rows, any order (column order matters)."""
    return sorted(map(_norm_row, expected)) == sorted(map(_norm_row, actual))


def _norm_row(row: list[Any]) -> str:
    """One row as a single comparable string."""
    return "|".join(map(_norm, row))


def _found(want: Counter, have: list[Counter]) -> bool:
    """True if some column in `have` holds exactly the values in `want`."""
    return any(want == col for col in have)


def complete(expected: Rows, actual: Rows) -> bool:
    """Same number of rows, and every expected column's values appear as some actual column.

    Forgives extra columns, column order and aliases; does not forgive wrong rows, a wrong count or a missing column.
    """
    if len(expected) != len(actual):
        return False
    have = _columns(actual)
    return all(_found(want, have) for want in _columns(expected))


def _identifying_column(names: list[str]) -> int:
    """Index of the column that says what the rows *are*: the first one that is not an id (``id``, ``user_id``, ...).

    A reference that starts with ``id, name, email`` is about the people, not the ids; a query that returns name and
    email without the id still answers the question. Falls back to column 0 if every column is an id.
    """
    return next((i for i, n in enumerate(names) if n != "id" and not n.endswith("_id")), 0)


def entities(expected: Rows, actual: Rows, names: list[str] | None = None) -> bool:
    """Same number of rows, and the expected identifying column appears as some actual column.

    The identifying column is the first non-id column of the reference (the people, the departments, the count),
    chosen with `names` (the reference's column names; column 0 if omitted). The gold queries are deliberately
    verbose (name, email, department, ...), so a leaner query that lists the right people is still a right answer.
    A wrong number of rows, or the wrong people, still fails.
    """
    if len(expected) != len(actual):
        return False
    if not expected:
        return True
    index = _identifying_column(names) if names else 0
    return _found(_columns(expected)[index], _columns(actual))


def grade(expected: Rows, actual: Rows, names: list[str] | None = None) -> str:
    """exact > complete > entities > wrong. `names` are the reference's column names (see :func:`entities`)."""
    if exact(expected, actual):
        return "exact"
    if complete(expected, actual):
        return "complete"
    if entities(expected, actual, names):
        return "entities"
    return "wrong"
