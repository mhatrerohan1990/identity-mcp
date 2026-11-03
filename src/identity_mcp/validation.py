"""Validate generated SQL before it runs.

Two stages, in order:
  1. static   Parsed and inspected, never executed: one read-only SELECT, known tables and columns, our own
              rules. Catches unsafe or hallucinated queries without touching the database.
  2. dry run  `EXPLAIN` on the read-only connection: SQLite compiles the query and reports anything the
              static stage could not see, still without executing it.

Failures are short, specific messages meant to be shown to the user and, later, fed back to the model.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

from .db import SCHEMA_SQL, connect_ro

# Statement and function kinds that must never appear, even nested in a query.
_FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge,
                    exp.Command, exp.Pragma, exp.Attach, exp.Transaction, exp.Set, exp.Use)
_FORBIDDEN_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
_NOW_NODES = (exp.CurrentDate, exp.CurrentTime, exp.CurrentTimestamp)


@dataclass
class Validation:
    ok: bool
    stage: str | None = None  # "static" or "dry_run" when ok is False
    errors: list[str] = field(default_factory=list)


def _load_schema() -> dict[str, dict[str, str]]:
    """{table: {column: type}} derived from schema.sql, so there is no second copy to drift."""
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA_SQL)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    schema = {t: {r[1]: r[2] for r in con.execute(f"PRAGMA table_info({t})")} for t in tables}
    con.close()
    return schema


SCHEMA = _load_schema()
_TABLES = {t.lower() for t in SCHEMA}


def validate_static(sql: str) -> list[str]:
    """Problems found by inspecting `sql` without running it. Empty list means it passed."""
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except SqlglotError as e:
        return [f"SQL syntax error: {str(e).splitlines()[0]}"]
    statements = [s for s in statements if s is not None]
    if not statements:
        return ["The query is empty."]
    if len(statements) > 1:
        return [f"Exactly one statement is allowed, found {len(statements)}."]
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        return [f"Only SELECT queries are allowed, got {type(tree).__name__.upper()}."]

    errors: list[str] = []
    for node in tree.find_all(*_FORBIDDEN_NODES):
        errors.append(f"{type(node).__name__.upper()} is not allowed inside a query.")
    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() in _FORBIDDEN_FUNCTIONS:
            errors.append(f"Function {fn.name}() is not allowed.")
    for node in tree.find_all(exp.Literal):
        if node.is_string and node.this.lower() == "now":
            errors.append("Do not use 'now': today is fixed at 2026-11-01. Write date('2026-11-01', ...) instead.")
            break
    if next(tree.find_all(*_NOW_NODES), None) is not None:
        errors.append("Do not use CURRENT_DATE/CURRENT_TIME/CURRENT_TIMESTAMP: today is fixed at 2026-11-01.")

    ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            errors.append("Table-valued functions are not allowed.")
        elif table.db and table.db.lower() != "main":
            errors.append(f"Schema-qualified table {table.db}.{table.name} is not allowed.")
        elif table.name.lower() not in _TABLES | ctes:
            errors.append(f"Unknown table {table.name!r}. Available tables: {', '.join(sorted(SCHEMA))}.")
    if errors:
        return errors

    try:  # resolves every column against its table; catches unknown columns and ambiguous or reused aliases
        qualify(tree.copy(), schema=SCHEMA, dialect="sqlite", validate_qualify_columns=True)
    except SqlglotError as e:
        message = re.sub(r"\s*Line: \d+, Col: \d+\.?", "", str(e)).rstrip(".")
        used = sorted({t.name.lower() for t in tree.find_all(exp.Table)} & _TABLES)
        available = "; ".join(f"{t}({', '.join(SCHEMA[t])})" for t in used)
        return [f"Column check failed: {message}. Columns available in the tables this query uses: {available}."]
    return []


def validate_dry_run(sql: str, con: sqlite3.Connection | None = None) -> list[str]:
    """Ask SQLite to compile the query (EXPLAIN) without running it."""
    own = con is None
    con = con or connect_ro()
    try:
        con.execute(f"EXPLAIN {sql}").fetchall()
        return []
    except sqlite3.Error as e:
        return [f"SQLite rejected the query: {e}"]
    finally:
        if own:
            con.close()


def validate(sql: str, con: sqlite3.Connection | None = None) -> Validation:
    """Static checks first; the dry run only happens if they pass."""
    errors = validate_static(sql)
    if errors:
        return Validation(False, "static", errors)
    errors = validate_dry_run(sql, con)
    if errors:
        return Validation(False, "dry_run", errors)
    return Validation(True)
