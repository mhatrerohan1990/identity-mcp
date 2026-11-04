"""Turn the graph's progress into plain events a screen (terminal or browser) can show.

The graph reports ``(node, update)`` pairs holding LangChain messages. Those are an internal detail, so this module
translates them into small JSON-friendly dicts, and also into one-line descriptions for the command line.

Event types (each a dict with a ``type`` key):

* ``tool_call``   - the model asked a tool a question: ``name``, ``question``, ``filter_from`` (``{"result_id", "column"}`` or None)
* ``tool_result`` - the tool answered: ``name``, ``scope``, ``row_count``, ``columns``, ``rows`` (first 25), ``truncated``,
  ``sql``, ``result_id``, and ``error`` (None when it worked)
* ``nudge``       - the model wrote plain text instead of calling a tool and was told to try again
* ``verification`` - the answer was checked against the results: ``ok`` and ``issues`` (plain sentences)
* ``revise``      - the check failed and the model was sent back to fix its answer once
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


def describe_call(call: dict[str, Any]) -> str:
    """A tool call as one readable line, e.g. ``query_activity("which of these ...", filter_from=r_9602de.id)``."""
    args = dict(call.get("args", {}))
    question = args.pop("question", "")
    link = args.pop("filter_from", None)
    parts = [repr(question)] if question else []
    if isinstance(link, dict) and link.get("result_id"):
        parts.append(f"filter_from={link.get('result_id')}.{link.get('column')}")
    parts += [f"{k}={v!r}" for k, v in args.items()]
    return f"{call['name']}({', '.join(parts)})"


def describe_result(message: ToolMessage) -> str:
    """A tool result as one readable line: the scope and row count, the result id, or the error."""
    try:
        body = json.loads(message.content)
    except (TypeError, json.JSONDecodeError):
        return str(message.content)[:200]
    if "error" in body:
        return f"{body.get('scope', 'tool')} error: {body['error']}"
    return f"{body.get('scope')}: {body.get('row_count')} row(s), columns {body.get('columns')}, result_id={body.get('result_id')}"


def result_event(message: ToolMessage) -> dict[str, Any]:
    """A tool result as a ``tool_result`` event. Text that is not the server's JSON becomes the ``error``."""
    event: dict[str, Any] = {"type": "tool_result", "name": message.name, "scope": None, "row_count": 0, "columns": [],
                             "rows": [], "truncated": False, "sql": None, "result_id": None, "error": None}
    try:
        body = json.loads(message.content)
    except (TypeError, json.JSONDecodeError):
        body = None
    if not isinstance(body, dict):
        event["error"] = str(message.content)[:500]
        return event
    for key in ("scope", "row_count", "columns", "rows", "truncated", "sql", "result_id", "error"):
        if key in body:
            event[key] = body[key]
    return event


def events_for(node: str, update: dict[str, Any]) -> list[dict[str, Any]]:
    """The events one graph node's `update` produced, in order (often none: the model's own text is not an event)."""
    events: list[dict[str, Any]] = []
    if node == "verify":
        return [{"type": "verification", "ok": not update.get("issues"), "issues": update.get("issues", [])}]
    for message in update.get("messages", []):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["name"] == "final_answer":
                    continue  # finishing is not a step the user needs to see
                args = call.get("args", {})
                link = args.get("filter_from")
                events.append({"type": "tool_call", "name": call["name"], "question": args.get("question", ""),
                               "filter_from": link if isinstance(link, dict) and link.get("result_id") else None})
        elif isinstance(message, ToolMessage):
            events.append(result_event(message))
        elif node in ("nudge", "revise"):
            events.append({"type": node})
    return events
