"""MCP server exposing two tools over the identity-mcp access-governance data.

* ``query_identity`` - people, groups, applications, entitlements and who has access to what.
* ``query_activity`` - what users did: logins, reads, writes and admin actions.

Each tool takes a plain-English question and answers it with the same guarded question-to-SQL pipeline, restricted to
its own tables (``scopes.py``). A question that needs both ("dormant admin accounts") is answered by the *client*
calling one tool and passing the result to the other: every result carries a ``result_id``, and a later call can name
it in ``filter_from`` to restrict itself to those ids (``handoff.py``). The orchestration (the reason-act loop) lives in
the client, as with any MCP server; the ``agent`` package is our own.

stdio (Claude Desktop / Claude Code):  uv run python -m identity_mcp.mcp_server
HTTP: mounted at /mcp by the FastAPI app (see api/app.py).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .context import handoff
from . import pipeline
from .core.logs import configure
from .guardrails.scopes import ACTIVITY, IDENTITY, Scope

SHOWN_ROWS = 25  # rows returned to the caller per call; the full result (up to 200 rows) stays available via result_id

mcp = MCPServer(
    "identity-mcp",
    instructions=(
        "Answers plain-English questions about an identity and access governance dataset through two tools: "
        "query_identity (users, groups, applications, entitlements, access grants) and query_activity (login, read, "
        "write and admin-action events). A question that needs both sides, such as dormant admins, takes two calls: "
        "run one tool, then pass its result_id as filter_from to the other. Each call returns rows, not prose."
    ),
)


class FilterFrom(BaseModel):
    """Restrict a query to ids taken from an earlier call's result.

    Attributes:
        result_id: The ``result_id`` returned by an earlier call (valid for about 30 minutes).
        column: The column of that result holding the ids, e.g. ``id`` or ``user_id``.
    """

    result_id: str = Field(description="result_id returned by an earlier call")
    column: str = Field(description="column of that result that holds the ids, e.g. 'id' or 'user_id'")


async def _run(scope: Scope, question: str, filter_from: FilterFrom | None) -> dict[str, Any]:
    """Answer `question` within `scope` and shape the result for a calling agent.

    Returns the rows (first ``SHOWN_ROWS``), the SQL that ran, ``row_count`` and ``truncated``, and a ``result_id`` that a
    later call can pass as ``filter_from``. No prose answer is written: the caller reasons over the rows itself. Problems
    (a bad ``filter_from``, a failed validation) come back as ``error`` with the reason, so the agent can react.
    """
    inputs = None
    if filter_from is not None:
        try:
            inputs = handoff.STORE.values(filter_from.result_id, filter_from.column)
        except handoff.HandoffError as e:
            return {"scope": scope.name, "question": question, "error": str(e)}
    out = await pipeline.ask(question, scope=scope, inputs=inputs, summarize_answer=False)
    result: dict[str, Any] = {"scope": scope.name, "question": question, "sql": out.get("sql")}
    for key in ("error", "warning", "validation_errors", "input_count"):
        if key in out:
            result[key] = out[key]
    if "rows" in out:
        rows = out["rows"]
        result.update(
            columns=out["columns"], rows=rows[:SHOWN_ROWS], row_count=out["row_count"], truncated=out["truncated"],
            shown=min(len(rows), SHOWN_ROWS), result_id=handoff.STORE.put(out["columns"], rows, out["truncated"]),
        )
    return result


_RETURNS = (
    f"Returns `columns`, `rows` (first {SHOWN_ROWS}), `row_count`, the `sql` that ran, and a `result_id`. "
    "Pass `filter_from` ({result_id, column}) to restrict the question to ids from an earlier call. "
    "If something failed, `error` says why."
)

IDENTITY_TOOL = (
    f"{IDENTITY.description}\n\n"
    'Examples: "Which active users have admin access?", "Who is in the Cloud Admins group?", "Which terminated users '
    'still have access to Snowflake?". Cannot answer anything about activity (logins, last seen); use query_activity.\n\n'
    + _RETURNS
)

ACTIVITY_TOOL = (
    f"{ACTIVITY.description}\n\n"
    'Examples: "How many failed logins were there in the last 30 days?", "When was each of these users last active?", '
    '"Which of these users had no activity in the last 90 days?" (with `filter_from`). Cannot answer anything about '
    "groups, entitlements or who has access; use query_identity.\n\n"
    "With `filter_from` the question is about those ids only, for example the admins found by query_identity. " + _RETURNS
)


@mcp.tool(description=IDENTITY_TOOL)
async def query_identity(question: str, filter_from: FilterFrom | None = None) -> dict[str, Any]:
    """Tool: answer a question about users, groups, applications, entitlements and access grants (see IDENTITY_TOOL)."""
    return await _run(IDENTITY, question, filter_from)


@mcp.tool(description=ACTIVITY_TOOL)
async def query_activity(question: str, filter_from: FilterFrom | None = None) -> dict[str, Any]:
    """Tool: answer a question about activity events (see ACTIVITY_TOOL); `filter_from` limits it to earlier ids."""
    return await _run(ACTIVITY, question, filter_from)


if __name__ == "__main__":
    configure()  # stderr only: stdout carries the MCP protocol
    mcp.run()
