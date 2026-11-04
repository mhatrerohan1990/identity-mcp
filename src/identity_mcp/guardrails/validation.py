"""Validate generated SQL before it runs, and sandbox the connection it runs on.

Three layers, in order:
  1. static   Parsed and inspected, never executed: length, one read-only SELECT, known tables and columns,
              joins with conditions, allowed functions, our own rules. Catches unsafe or hallucinated queries
              without touching the database, with messages written to be fed back to the model.
  2. dry run  `EXPLAIN` on the sandboxed connection: SQLite itself compiles the query without executing it.
  3. sandbox  The connection used for the dry run AND for execution has an authorizer (only SELECT, reads of
              known tables, allowed functions) and a size limit. It uses SQLite's own parser, so it holds even
              if sqlglot reads a query differently from SQLite.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

from ..core.db import SCHEMA_SQL, connect_ro
from ..core.logs import flow

if TYPE_CHECKING:  # scopes.py imports this module (for SCHEMA), so the import is only for type hints
    from .scopes import Scope

MAX_SQL_CHARS = 4000
MAX_VALUE_BYTES = 1_000_000  # engine limit on any string/blob a query builds (stops randomblob(1e9) and friends)

# Statement kinds that must never appear, even nested in a query.
_FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge,
                    exp.Command, exp.Pragma, exp.Attach, exp.Transaction, exp.Set, exp.Use)
_NOW_NODES = (exp.CurrentDate, exp.CurrentTime, exp.CurrentTimestamp)

# The only SQL functions a query may call. Anything else (load_extension, randomblob, zeroblob, printf, ...) is refused
# by the static check when sqlglot sees it as an unknown function, and by the SQLite authorizer regardless.
ALLOWED_FUNCTIONS = frozenset("""
    count sum avg min max total group_concat
    date datetime time julianday unixepoch strftime
    round abs ceil ceiling floor coalesce ifnull nullif iif
    length lower upper substr substring instr trim ltrim rtrim replace typeof concat concat_ws like glob
    row_number rank dense_rank percent_rank cume_dist ntile lag lead first_value last_value nth_value
""".split())
_COMPARISONS = (exp.EQ,)


class ValidationError(ValueError):
    """Raised when SQL that was never validated is handed to the executor."""

    def __init__(self, errors: list[str]):
        super().__init__(" ".join(errors))
        self.errors = errors


@dataclass
class Validation:
    """Result of checking a query before it runs.

    Attributes:
        ok: True when the query may be executed.
        stage: Which check rejected it: ``static``, ``dry_run`` or (added by ``pipeline.check_sql``) ``grounding``.
        errors: Plain-language reasons, written so the model can act on them in the repair loop.
    """
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


# ----------------------------------------------------------------------------------------------- sandbox
def _authorizer(forbidden: frozenset[str]):
    """SQLite authorizer: SELECT only, reads of known tables, allowed functions.

    `forbidden` is every real object in the database that is not part of the schema (sqlite_master, ...). A CTE or
    subquery name is not a real object, so recursive queries over their own name keep working.
    """
    def authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        """SQLite calls this for every operation while compiling a query; return OK or DENY (the action codes are SQLite's).
        """
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            name = (arg1 or "").lower()
            return sqlite3.SQLITE_DENY if name in forbidden or name.startswith("sqlite_") else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY  # writes, DDL, PRAGMA, ATTACH, transactions, ...
    return authorize


def connect_query(scope: Scope | None = None) -> sqlite3.Connection:
    """Read-only connection for running model-written SQL: authorizer plus a limit on value sizes.

    With a `scope`, the tables outside it are added to the authorizer's forbidden set, so SQLite itself refuses to read
    them whatever the static checks concluded.
    """
    con = connect_ro()
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    real = {r[0].lower() for r in con.execute("SELECT name FROM sqlite_master")}
    forbidden = real - _TABLES
    if scope is not None:
        forbidden |= _TABLES - {t.lower() for t in scope.tables}
    con.set_authorizer(_authorizer(frozenset(forbidden)))
    return con


def normalize(sql: str) -> str:
    """Trim whitespace and trailing semicolons (a trailing comment is left alone: SQLite accepts it)."""
    return re.sub(r"[;\s]+$", "", sql.strip())


# ------------------------------------------------------------------------------------------------ static
def _links(where: exp.Expression | None, alias: str) -> bool:
    """True if the WHERE clause equates a column of `alias` with a column of another table (old-style join)."""
    if where is None:
        return False
    for cmp in where.find_all(*_COMPARISONS):
        left, right = cmp.left, cmp.right
        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            qualifiers = {left.table.lower(), right.table.lower()}
            if alias in qualifiers and len(qualifiers) == 2:
                return True
    return False


def _join_problems(tree: exp.Expression) -> list[str]:
    """Report joins that would pair every row with every row.

    A join is fine if it has an ``ON`` containing a column, ``USING``, ``NATURAL``, or (for comma/CROSS joins) a
    ``WHERE`` equality linking the table to another. ``ON TRUE`` and ``ON 1 = 1`` do not count. This is the usual
    cause of queries that run until the timeout.

    Returns:
        One message per offending join; empty when all joins are conditioned.
    """
    problems = []
    for select in tree.find_all(exp.Select):
        for join in select.args.get("joins") or []:
            on = join.args.get("on")
            real_condition = on is not None and next(on.find_all(exp.Column), None) is not None  # ON TRUE / ON 1=1 are not
            if real_condition or join.args.get("using") or str(join.args.get("method") or "").upper() == "NATURAL":
                continue
            alias = join.this.alias_or_name.lower()
            if _links(select.args.get("where"), alias):
                continue
            problems.append(
                f"The join with {join.this.alias_or_name!r} has no join condition, so every row would be paired with every "
                "row (a cartesian product). Add ON with the matching key columns, e.g. ON g.user_id = u.id."
            )
    return problems


def validate_static(sql: str, scope: Scope | None = None) -> list[str]:
    """Problems found by inspecting `sql` without running it. Empty list means it passed.

    With a `scope`, a query that names a table outside it is rejected, with the tables the scope does cover.
    """
    if len(sql) > MAX_SQL_CHARS:
        return [f"The query is too long ({len(sql)} characters, maximum {MAX_SQL_CHARS}). Write a simpler query."]
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except SqlglotError as e:
        return [f"SQL syntax error: {str(e).splitlines()[0]}"]
    except RecursionError:
        return ["The query is too deeply nested to analyze. Write a simpler query."]
    # a trailing "; -- comment" parses as an extra empty statement: ignore those
    statements = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
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
    unknown_functions = sorted({fn.name.lower() for fn in tree.find_all(exp.Anonymous)} - ALLOWED_FUNCTIONS)
    for name in unknown_functions:
        errors.append(
            f"Function {name}() is not allowed. Use plain aggregates and date functions such as count, sum, date, julianday, strftime."
        )
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
        elif scope is not None and table.name.lower() not in {t.lower() for t in scope.tables} | ctes:
            errors.append(
                f"Table {table.name!r} is not available in the {scope.name} tool. "
                f"This tool covers: {', '.join(sorted(scope.tables))}."
            )
    if errors:
        return errors
    errors = _join_problems(tree)
    if errors:
        return errors

    try:  # resolves every column against its table; catches unknown columns and ambiguous or reused aliases
        qualify(tree.copy(), schema=SCHEMA, dialect="sqlite", validate_qualify_columns=True)
    except SqlglotError as e:
        return [_column_error(tree, sql, str(e))]
    except RecursionError:
        return ["The query is too deeply nested to analyze. Write a simpler query."]
    return []


def _column_error(tree: exp.Expression, sql: str, raw: str) -> str:
    """Turn sqlglot's terse message into one that names the problem and says how to fix it."""
    message = re.sub(r"\s*Line: \d+, Col: \d+\.?", "", raw).rstrip(".")
    aliases = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
    used = sorted(set(aliases.values()) & _TABLES)
    tables = "; ".join(f"{t}({', '.join(SCHEMA[t])})" for t in used)

    reused = re.search(r"Alias already used: (\w+)", message)
    if reused:
        return (f"Alias {reused.group(1)!r} is used for more than one table or subquery. Give each its own alias. "
                f"Tables in this query: {tables}.")

    missing = re.search(r"Unknown column: (\w+)|Column '(\w+)' could not be resolved", message)
    if not missing:
        return f"Column check failed: {message}. Columns available in the tables this query uses: {tables}."
    name = (missing.group(1) or missing.group(2)).lower()
    where = sorted(t for t, cols in SCHEMA.items() if name in {c.lower() for c in cols})
    in_query = [t for t in where if t in used]

    # sqlglot says "could not be resolved" for an ambiguous column too: tell the two apart
    unqualified = [c for c in tree.find_all(exp.Column) if c.name.lower() == name and not c.table]
    if unqualified and len(in_query) > 1:
        quals = [a for a, t in aliases.items() if t in in_query]
        return (f"Column {name!r} is ambiguous: it exists on {', '.join(in_query)}. "
                f"Qualify it with a table alias, e.g. {' or '.join(f'{a}.{name}' for a in quals)}.")

    # SQLite reads "double quotes" as an identifier first: a value in double quotes looks like a missing column
    if re.search(rf'"{re.escape(name)}"', sql, re.I) and not in_query:
        return (f'"{name}" is read as a column name, but there is no such column. Text values must use single '
                f"quotes: '{name}'. Tables in this query: {tables}.")

    # name the offending reference(s), e.g. "i.integration (i is entitlements)"
    refs = []
    for col in tree.find_all(exp.Column):
        if col.name.lower() != name:
            continue
        table = aliases.get(col.table.lower())
        if col.table and table in SCHEMA and name in {c.lower() for c in SCHEMA[table]}:
            continue  # a valid reference to the same column name on a table that has it
        if not col.table and in_query:
            continue
        refs.append(f"{col.table}.{col.name} ({col.table} is {table})" if table else col.sql())
    text = f"Column check failed: {', '.join(dict.fromkeys(refs)) or repr(name)} does not exist."
    text += f" Tables in this query: {tables}."
    if where:
        text += f" A column named {name!r} exists on: {', '.join(where)}."
        text += " Read it from that table's alias instead." if in_query else " Join that table to use it."
    else:
        text += f" There is no column named {name!r} in any table."
    return text


# ------------------------------------------------------------------------------------------ dry run + all
def validate_dry_run(sql: str, con: sqlite3.Connection | None = None, scope: Scope | None = None) -> list[str]:
    """Ask SQLite to compile the query (EXPLAIN) on the sandboxed connection (limited to `scope`), without running it."""
    own = con is None
    con = con or connect_query(scope)
    try:
        con.execute(f"EXPLAIN {normalize(sql)}").fetchall()
        return []
    except sqlite3.Error as e:
        return [f"SQLite rejected the query: {e}"]
    finally:
        if own:
            con.close()


def validate(sql: str, con: sqlite3.Connection | None = None, scope: Scope | None = None) -> Validation:
    """Static checks first; the dry run only happens if they pass. Fails closed if the checker itself errors.

    `scope` limits the query to one tool's tables (see ``scopes.py``); without it every table is allowed.
    """
    try:
        errors = validate_static(sql, scope)
    except Exception as e:  # a crash in analysis must reject the query, never let it through or take the request down
        flow.exception("static validation crashed on %r", sql[:200])
        return Validation(False, "static", [f"The query could not be analyzed ({type(e).__name__}). Write a simpler query."])
    if errors:
        return Validation(False, "static", errors)
    errors = validate_dry_run(sql, con, scope)
    if errors:
        return Validation(False, "dry_run", errors)
    return Validation(True)
