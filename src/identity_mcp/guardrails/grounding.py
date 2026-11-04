"""Value grounding: catch literals the data does not contain.

Validation proves a query is *well-formed*. This layer checks that the values it filters on *exist*:
`WHERE department = 'Eng'` is valid SQL that quietly returns nothing, because the department is 'Engineering'.
For every small text column (department, title, status, application and group names, ...) we keep the real set of
values, and any `col = 'x'`, `col <> 'x'` or `col IN ('x', 'y')` with a value outside it is reported with the
closest real values, so the model can correct it through the repair loop.

Unlike validation this layer is a convenience, not a safety net: if the catalog cannot be built the query is let
through (and a warning is logged).
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import threading
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from ..core.db import db_path
from ..core.logs import flow
from .validation import SCHEMA

MAX_DISTINCT = 60  # a text column with more distinct values than this is free text (names, emails), not a category
MAX_VALUE_LEN = 60
LIST_ALL_UP_TO = 15  # list every value in the message when a column has this few
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")

Catalog = dict[tuple[str, str], frozenset[str]]  # (table, column) -> every value that column holds


def build_catalog(con: sqlite3.Connection) -> Catalog:
    """The distinct values of every categorical text column in `con`."""
    catalog: Catalog = {}
    for table, columns in SCHEMA.items():
        for column, type_ in columns.items():
            if type_.upper() != "TEXT":
                continue
            rows = con.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT {MAX_DISTINCT + 1}'
            ).fetchall()
            values = [r[0] for r in rows]
            if len(values) > MAX_DISTINCT or any(len(v) > MAX_VALUE_LEN or _DATE_LIKE.match(v) for v in values):
                continue
            if values:
                catalog[(table, column)] = frozenset(values)
    return catalog


_cache: tuple[tuple[str, float], Catalog] | None = None
_lock = threading.Lock()


def catalog() -> Catalog | None:
    """The catalog for the current database, rebuilt when the file changes. None if it cannot be built."""
    global _cache
    path = db_path()
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return None
    with _lock:
        if _cache is None or _cache[0] != key:
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    _cache = (key, build_catalog(con))
                finally:
                    con.close()
            except sqlite3.Error:
                flow.warning("value grounding disabled: could not read values from %s", path, exc_info=True)
                return None
        return _cache[1]


def _string(node: exp.Expression) -> str | None:
    """The text of `node` if it is a quoted string literal, else None (numbers, columns and expressions give None)."""
    return node.this if isinstance(node, exp.Literal) and node.is_string else None


def _literal_checks(tree: exp.Expression) -> list[tuple[str, str, str]]:
    """(table, column, literal) for every column-vs-string-literal comparison, columns resolved to real tables."""
    try:
        qualified = qualify(tree.copy(), schema=SCHEMA, dialect="sqlite")
        scopes = traverse_scope(qualified)
    except (SqlglotError, RecursionError):
        return []
    owner: dict[int, str] = {}  # id(column node) -> real table name
    for scope in scopes:
        for col in scope.columns:
            source = scope.sources.get(col.table)
            if isinstance(source, exp.Table):
                owner[id(col)] = source.name
    found = []
    for node in qualified.find_all(exp.EQ, exp.NEQ, exp.In):
        if isinstance(node, exp.In):
            column, literals = node.this, [_string(e) for e in node.expressions]
        else:
            sides = (node.left, node.right)
            column = next((s for s in sides if isinstance(s, exp.Column)), None)
            other = next((s for s in sides if s is not column), None)
            literals = [_string(other)] if other is not None else []
        if isinstance(column, exp.Column) and id(column) in owner:
            found += [(owner[id(column)], column.name, lit) for lit in literals if lit is not None]
    return found


def _suggest(literal: str, values: frozenset[str]) -> list[str]:
    """Real values that `literal` was probably meant to be.

    Case-insensitive matches come first and are returned alone (the usual mistake is ``'ACTIVE'`` for ``'active'``).
    Otherwise: close spellings (``difflib``, cutoff 0.6) plus values that contain, or are contained in, the literal
    (``'Eng'`` -> ``'Engineering'``). At most 5, best first.
    """
    ordered = sorted(values)
    exact_ci = [v for v in ordered if v.lower() == literal.lower()]
    if exact_ci:
        return exact_ci
    close = difflib.get_close_matches(literal, ordered, n=3, cutoff=0.6)
    partial = [v for v in ordered if literal.lower() in v.lower() or v.lower() in literal.lower()]
    return list(dict.fromkeys(close + partial))[:5]


def _message(table: str, column: str, literal: str, values: frozenset[str]) -> str:
    """The text sent back to the model (and shown to the user) for one unknown value.

    Names the column and the bad value, adds the closest real values, lists every value when the column is small
    (``LIST_ALL_UP_TO``), and reminds the model that values are case-sensitive and that a value which does not exist
    in the data should be left alone instead of being swapped for something else.
    """
    text = f"{table}.{column} has no value {literal!r}."
    suggestions = _suggest(literal, values)
    if suggestions:
        text += " Closest real values: " + ", ".join(repr(s) for s in suggestions) + "."
    if len(values) <= LIST_ALL_UP_TO:
        text += " All values: " + ", ".join(repr(v) for v in sorted(values)) + "."
    text += " Values are case-sensitive. If the question names something that is not in the data, keep it as is."
    return text


def check(sql: str, values: Catalog | None = None) -> list[str]:
    """Problems with the literal values `sql` filters on. Empty list means grounded (or nothing to check)."""
    values = catalog() if values is None else values
    if not values:
        return []
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
        checks = _literal_checks(tree)
    except (SqlglotError, RecursionError):
        return []  # malformed SQL is validation's job
    errors = []
    for table, column, literal in dict.fromkeys(checks):  # drop repeats, keep order
        known = values.get((table, column))
        if known is not None and literal not in known:
            errors.append(_message(table, column, literal, known))
    return errors
