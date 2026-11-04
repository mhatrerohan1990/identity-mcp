"""``POST /api/agent``: ask the LangGraph agent a question and stream its steps as they happen.

The response is NDJSON (one JSON object per line, ``application/x-ndjson``): the ``tool_call`` / ``tool_result`` / ``nudge``
events from ``agent.trace``, then one ``answer`` or ``error`` event from ``agent.session``. The chat page reads it
line by line to draw a live trace above the answer.

The agent reaches the MCP server the same way the command line does: a stdio subprocess by default, or the URL in
``$IDENTITY_MCP_AGENT_SERVER`` (for example this app's own ``/mcp``). Needs ``uv sync --extra agent``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["chat"])


class AgentTurn(BaseModel):
    """One finished turn of the chat: what was asked and the answer given."""
    question: str
    answer: str


class AgentRequest(BaseModel):
    """Body of ``POST /api/agent``.

    Attributes:
        question: One non-empty question of at most 1000 characters.
        history: Earlier turns, oldest first (at most 6 accepted; the last 3 are used). Extra fields on a turn (rows,
            columns) are ignored, so the page can send the same history it sends to ``/api/ask``.
    """
    question: str = Field(min_length=1, max_length=1000)
    history: list[AgentTurn] = Field(default_factory=list, max_length=6)


HISTORY_USED = 3


async def event_lines(body: AgentRequest) -> AsyncIterator[str]:
    """The agent's events for `body` as NDJSON lines. A missing optional dependency becomes an ``error`` event, not a 500."""
    try:
        from ..agent import session  # imported here: langgraph is an optional extra, and the rest of the API works without it
    except ImportError as e:
        yield json.dumps({"type": "error", "message": f"The agent is not installed ({e}). Run: uv sync --extra agent"}) + "\n"
        return
    history = [(t.question, t.answer) for t in body.history[-HISTORY_USED:]]
    async for event in session.events(body.question.strip(), history):
        yield json.dumps(event, default=str) + "\n"


@router.post("/api/agent")
async def agent(body: AgentRequest):
    """Run the agent on one question and stream its steps and answer as NDJSON."""
    return StreamingResponse(event_lines(body), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})
