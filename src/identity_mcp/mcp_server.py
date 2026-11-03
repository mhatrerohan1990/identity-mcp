"""MCP server exposing one tool, `ask`, over the identity-mcp access-governance data.

stdio (Claude Desktop / Claude Code):  uv run python -m identity_mcp.mcp_server
HTTP: mounted at /mcp by the FastAPI app (see api/app.py).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import pipeline

mcp = MCPServer(
    "identity-mcp",
    instructions=(
        "Answers plain-English questions about an identity and access governance dataset: users, groups, "
        "applications, entitlements, access grants and activity events. Call `ask` with the user's question."
    ),
)


@mcp.tool()
async def ask(question: str) -> dict[str, Any]:
    """Answer a plain-English question about users, groups, applications, entitlements, access grants
    and activity events (e.g. "Which terminated users still have access?").

    Returns `answer` (natural language), plus the `sql` that was run and the result `columns`/`rows`.
    If something failed, `error` explains why.
    """
    return await pipeline.ask(question)


if __name__ == "__main__":
    mcp.run()
