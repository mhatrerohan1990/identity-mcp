"""Running a checked query: read-only sandbox, a time limit and a row cap.

``execute`` re-runs the static checks itself, so nothing can reach the database unvalidated. Runtime failures (timeout,
size limit) come back as ``QueryError`` with a reason a user can read.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from ..context import handoff
from ..guardrails.scopes import Scope
from ..guardrails.validation import MAX_VALUE_BYTES, ValidationError, connect_query, normalize, validate_static

MAX_ROWS = 200  # rows returned to the caller
QUERY_TIMEOUT_S = 5.0


class QueryError(RuntimeError):
    """A validated query that SQLite could not complete (timeout, size limit, ...), with a readable reason."""


def _explain_failure(e: sqlite3.Error) -> str:
    """Turn a SQLite runtime error into a sentence for the user: timeout, value-size limit, sandbox refusal, or the raw error.
    """
    text = str(e)
    if "interrupted" in text:
        return f"The query ran longer than {QUERY_TIMEOUT_S:g} seconds and was stopped. It is probably missing a join condition or scanning far too much."
    if "too big" in text:
        return f"The query built a value larger than the {MAX_VALUE_BYTES // 1_000_000} MB limit."
    if "not authorized" in text:
        return f"SQLite refused an operation the sandbox does not allow: {text}."
    return f"{type(e).__name__}: {text}"


def execute(sql: str, scope: Scope | None = None, inputs: handoff.Input | None = None) -> dict[str, Any]:
    """Run `sql` on the sandboxed read-only connection with a time limit and row cap.

    Refuses anything that fails the static checks, so no caller can run unvalidated SQL by accident. With a `scope`
    the query may read only that scope's tables (checked statically and again by SQLite's authorizer); with `inputs`
    the values are exposed to the query as the ``input`` table (see ``handoff.py``).
    """
    sql = handoff.with_input(sql, inputs)
    errors = validate_static(sql, scope)
    if errors:
        raise ValidationError(errors)
    con = connect_query(scope)
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = con.execute(normalize(sql))
        columns = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(MAX_ROWS + 1)]
    except (sqlite3.OperationalError, sqlite3.DataError) as e:
        raise QueryError(_explain_failure(e)) from e
    finally:
        con.close()
    truncated = len(rows) > MAX_ROWS
    return {"columns": columns, "rows": rows[:MAX_ROWS], "row_count": len(rows[:MAX_ROWS]), "truncated": truncated}
