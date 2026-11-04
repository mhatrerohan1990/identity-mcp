"""Run the agent for one question and report its progress as events.

This is what the web API uses: it connects to the server, builds the graph, and yields the events from ``trace`` as they
happen, ending with an ``answer`` event (or an ``error`` event; the stream never raises). The command line has its own
printer but shares the model choice from here.

Final event types, in addition to those in ``trace``:

* ``answer`` - ``text``, ``steps`` (model calls used), ``elapsed_ms``, ``verified`` (True: passed the check against
  the data; False: it did not; None: it was not checked, as when the step budget ran out) and ``issues`` (what was still wrong if not; the text then ends with a caveat)
* ``error``  - ``message``: the run could not complete (the model server or the MCP server was unreachable, for example)
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_ollama import ChatOllama

from ..core import llm
from . import graph as agent_graph
from .tools import open_tools
from .trace import events_for

log = logging.getLogger(__name__)

MODEL_ENV = "IDENTITY_MCP_AGENT_MODEL"  # the agent's model; defaults to the same model as SQL generation


def default_model() -> str:
    """The Ollama model the agent uses: ``$IDENTITY_MCP_AGENT_MODEL``, else the SQL model."""
    return os.environ.get(MODEL_ENV, llm.MODEL)


def make_model(name: str | None = None) -> ChatOllama:
    """The chat model for the agent (deterministic, ``temperature=0``)."""
    return ChatOllama(model=name or default_model(), base_url=llm.BASE_URL, temperature=0)


async def events(
    question: str,
    history: Sequence[tuple[str, str]] = (),
    *,
    model: Any = None,
    tools_target: Any = None,
    max_steps: int = agent_graph.DEFAULT_MAX_STEPS,
) -> AsyncIterator[dict[str, Any]]:
    """Answer `question`, yielding an event for each step and a last ``answer`` or ``error`` event.

    Args:
        question: What the user asked.
        history: Earlier ``(question, answer)`` turns, oldest first.
        model: A chat model; defaults to :func:`make_model`. Tests pass a scripted one.
        tools_target: Where the MCP server is, as in ``tools.open_tools``; defaults to ``tools.server_target()``.
        max_steps: Most model calls per question.
    """
    started = time.monotonic()
    final, steps, issues, checked = "", 0, [], False
    try:
        async with open_tools(tools_target) as tools:
            graph = agent_graph.build_graph(model or make_model(), tools, max_steps)
            async for node, update in agent_graph.stream(graph, question, history):
                for event in events_for(node, update):
                    yield {**event, "elapsed_ms": int((time.monotonic() - started) * 1000)}
                final = agent_graph.answer_of(update) or final
                if node == "verify":
                    issues, checked = update.get("issues", []), True
                if node == "agent":
                    steps += 1
    except Exception as e:  # noqa: BLE001 - a stream that raises half way cannot report the failure to the browser
        log.exception("agent run failed")
        yield {"type": "error", "message": f"The agent could not finish: {e}"}
        return
    yield {"type": "answer", "text": final, "steps": steps, "verified": (not issues) if checked else None, "issues": issues, "elapsed_ms": int((time.monotonic() - started) * 1000)}
