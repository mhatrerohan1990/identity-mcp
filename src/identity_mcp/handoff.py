"""Handing one tool's result to the next tool.

"Show me dormant admins" needs two tools in turn: first the admins (identity), then which of them have no recent
activity (activity). The second step must filter on the first step's ids. Letting the calling model copy dozens of ids
from one tool call into the next is slow and error-prone, so instead:

1. a tool that returns rows also returns a short ``result_id``; the rows are kept here for a while (``ResultStore``);
2. the next tool takes ``filter_from={result_id, column}``; the server looks the values up *itself*;
3. those values reach the SQL as a table named ``input`` with one column, ``value``: the pipeline prepends
   ``WITH input(value) AS (VALUES (1), (2), ...)`` to the model's query (``with_input``).

Because ``input`` is an ordinary table inside the query, both questions work: "which of these users have activity" and
"which of these users have *no* activity" (``SELECT ... FROM input WHERE NOT EXISTS (...)``). The second is a set
difference that no single scoped tool could express across two stores otherwise.

Limits: the store lives in this process's memory, so it is shared by the tools of one server process but not across
several worker processes or restarts; entries expire after ``TTL_SECONDS`` and at most ``MAX_STORED`` are kept.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_INPUT_VALUES = 500  # values handed to a query; a result cap of 200 rows means normal use stays well below this
TTL_SECONDS = 30 * 60
MAX_STORED = 50


class HandoffError(ValueError):
    """The values requested from an earlier result cannot be provided (unknown or expired id, missing column, ...).

    The message is written for the calling model, which can react to it (for example by re-running the first step).
    """


@dataclass(frozen=True)
class Input:
    """Values passed from an earlier result into a query.

    Attributes:
        values: The ids (integers or strings) to expose as the ``input`` table.
        column: The name of the column they came from, e.g. ``id`` or ``user_id``; used to tell the model what they are.
        count: How many values there are; also used when describing the table to the model.
    """

    values: tuple[Any, ...]
    column: str

    @property
    def count(self) -> int:
        """Number of values."""
        return len(self.values)


@dataclass
class StoredResult:
    """One kept result: its columns and rows, whether it was cut short, and when it was stored (``time.monotonic``)."""

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    stored_at: float


class ResultStore:
    """A small, thread-safe, expiring in-memory store of query results, addressed by short ids."""

    def __init__(self, ttl: float = TTL_SECONDS, capacity: int = MAX_STORED) -> None:
        """Create a store whose entries expire after `ttl` seconds and which holds at most `capacity` results."""
        self._ttl, self._capacity = ttl, capacity
        self._items: dict[str, StoredResult] = {}
        self._lock = threading.Lock()

    def _evict(self) -> None:
        """Drop expired entries, then the oldest ones beyond capacity. Caller holds the lock."""
        now = time.monotonic()
        for key in [k for k, v in self._items.items() if now - v.stored_at > self._ttl]:
            del self._items[key]
        while len(self._items) > self._capacity:
            del self._items[next(iter(self._items))]  # dicts keep insertion order: the first key is the oldest

    def put(self, columns: list[str], rows: list[list[Any]], truncated: bool = False) -> str:
        """Keep a result and return its id (for example ``r_3f9a1c``)."""
        with self._lock:
            self._evict()
            result_id = "r_" + secrets.token_hex(3)
            self._items[result_id] = StoredResult(list(columns), [list(r) for r in rows], truncated, time.monotonic())
            self._evict()
            return result_id

    def values(self, result_id: str, column: str) -> Input:
        """The values of `column` in a stored result, ready to pass to a query.

        Raises:
            HandoffError: if the id is unknown or expired, the column is not in the result, the values are not ids
                (integers or text), or there are more than ``MAX_INPUT_VALUES``.
        """
        with self._lock:
            self._evict()
            stored = self._items.get(result_id)
        if stored is None:
            raise HandoffError(f"Result {result_id!r} was not found; it may have expired. Run the earlier query again.")
        names = {c.lower(): i for i, c in enumerate(stored.columns)}
        if column.lower() not in names:
            raise HandoffError(f"Result {result_id!r} has no column {column!r}. Its columns are: {', '.join(stored.columns)}.")
        if not is_key_column(column):
            raise HandoffError(
                f"Column {column!r} is not an id: people and applications can share a name, so only 'id', '*_id' or "
                f"'email' columns can be used as a filter. Run the earlier query again so that it returns an id column."
            )
        index = names[column.lower()]
        values = [row[index] for row in stored.rows if row[index] is not None]
        if any(isinstance(v, bool) or not isinstance(v, (int, str)) for v in values):
            raise HandoffError(f"Column {column!r} does not hold ids (integers or text), so it cannot be used as a filter.")
        values = list(dict.fromkeys(values))  # distinct, order kept
        if len(values) > MAX_INPUT_VALUES:
            raise HandoffError(f"Result {result_id!r} has {len(values)} distinct values in {column!r}; the limit is {MAX_INPUT_VALUES}.")
        return Input(tuple(values), stored.columns[index])


STORE = ResultStore()


def is_key_column(name: str) -> bool:
    """True for a column that identifies rows: ``id``, ``something_id`` or ``email``. Names are not unique here."""
    name = name.lower()
    return name in ("id", "email") or name.endswith("_id")


def _literal(value: Any) -> str:
    """A value as an SQL literal: integers as digits, text single-quoted with quotes doubled."""
    if isinstance(value, int):
        return str(int(value))
    return "'" + str(value).replace("'", "''") + "'"


_WITH = re.compile(r"^\s*WITH\s+(RECURSIVE\s+)?", re.IGNORECASE)


def with_input(sql: str, inp: Input | None) -> str:
    """`sql` with the ``input(value)`` table defined in front of it. Without an input, `sql` is returned unchanged.

    If the query already starts with ``WITH``, the table is added to its list of common table expressions, so queries
    that use their own CTEs keep working. With no values the table exists but is empty.
    """
    if inp is None:
        return sql
    body = "VALUES " + ", ".join(f"({_literal(v)})" for v in inp.values) if inp.values else "SELECT NULL WHERE 0"
    definition = f"input(value) AS ({body})"
    match = _WITH.match(sql)
    if match:
        return f"WITH {match.group(1) or ''}{definition}, " + sql[match.end():]
    return f"WITH {definition} {sql}"


def describe(inp: Input) -> str:
    """The sentence added to the SQL prompt so the model knows what the ``input`` table is and how to use it."""
    return (
        f"A table named input(value) is available. It holds {inp.count} ids taken from the column '{inp.column}' of an "
        "earlier step's result. Use it instead of typing the ids: restrict with `WHERE x IN (SELECT value FROM input)`, "
        "or to find which of them have NO matching rows, start from input "
        "(`FROM input i WHERE NOT EXISTS (...)`). Match `value` to the id column of the table it refers to."
    )


def unused_input(sql: str, inp: Input | None) -> list[str]:
    """Error messages if `sql` was given an ``input`` table but never reads it.

    A model told "restrict to these ids" will sometimes write the question's query without them, which silently answers
    about everyone instead of the ids from the earlier step. Such a query is rejected so the repair loop can fix it.
    Pass the query *before* ``with_input`` is applied. Returns an empty list when there is no input or it is used.
    """
    if inp is None:
        return []
    try:
        tree = sqlglot.parse_one(with_input(sql, inp), read="sqlite")
    except (SqlglotError, RecursionError):
        return []  # malformed SQL is validation's job
    if any(t.name.lower() == "input" for t in tree.find_all(exp.Table)):
        return []
    return [
        f"The query ignores the input table. It must use it: the question is about the {inp.count} ids in input(value), "
        "not about everyone. Restrict to them with `WHERE <id column> IN (SELECT value FROM input)`, or start from "
        "`FROM input i ...` (for example with `NOT EXISTS` to find which have no matching rows)."
    ]
