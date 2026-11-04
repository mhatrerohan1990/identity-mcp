"""Rescuing tool calls that a local model wrote as text.

Some models (``qwen2.5-coder`` through Ollama, for one) decide to call a tool but emit the call as JSON in the message
body instead of the structured ``tool_calls`` field. Left alone, the loop reads that as a plain answer and stops. This
turns such a reply into a real tool call, but only when the JSON names one of the tools we actually offered, so ordinary
prose, or JSON that is not a call, is never touched.

A reply that is only the call is recovered, and so is a call in a Markdown code fence or ``<tool_call>`` tags with ordinary
prose around it ("I apologize. Let's call the tool: ```json {...} ```"), which is how these models usually apologise and
act in one message. JSON mixed into a sentence without a fence or tags is left alone, and so is any reply whose fenced
blocks are not all calls to a tool we offered.

Recognised shapes::

    {"name": "query_identity", "arguments": {"question": "..."}}
    {"name": "query_identity", "parameters": {"question": "..."}}
    [{"name": ...}, {"name": ...}]
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_TAGS = re.compile(r"^<tool_call>\s*(.*?)\s*</tool_call>$", re.DOTALL)
_EMBEDDED = re.compile(r"```(?:json)?\s*(.*?)\s*```|<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _parse(text: str) -> Any:
    """The JSON in `text` after removing a code fence or ``<tool_call>`` tags, or None if it is not JSON."""
    text = text.strip()
    for wrapper in (_FENCE, _TAGS):
        match = wrapper.match(text)
        if match:
            text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _embedded(text: str) -> list[Any] | None:
    """The JSON values of every code fence / ``<tool_call>`` block in `text`, or None if there is none or one is not JSON."""
    blocks = [a or b for a, b in _EMBEDDED.findall(text)]
    if not blocks:
        return None
    values = [_parse(block) for block in blocks]
    return None if any(v is None for v in values) else values


def recover_tool_calls(reply: AIMessage, names: set[str]) -> AIMessage:
    """`reply` with its text-form tool calls converted to real ones; unchanged if there is nothing to recover.

    Args:
        reply: The model's reply.
        names: The tool names that were offered. A call to any other name is not recovered.
    """
    if reply.tool_calls or not isinstance(reply.content, str) or not reply.content.strip():
        return reply
    parsed = _parse(reply.content)
    if parsed is None:  # not purely JSON: look for fenced or tagged calls inside surrounding prose
        blocks = _embedded(reply.content)
        if blocks is None:
            return reply
        parsed = [item for block in blocks for item in (block if isinstance(block, list) else [block])]
    items = parsed if isinstance(parsed, list) else [parsed]
    calls = []
    for item in items:
        if not isinstance(item, dict) or item.get("name") not in names:
            return reply  # anything that is not entirely a call to a known tool stays as the model wrote it
        args = item.get("arguments", item.get("parameters", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return reply
        if not isinstance(args, dict):
            return reply
        calls.append({"name": item["name"], "args": args, "id": f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"})
    return AIMessage(content="", tool_calls=calls) if calls else reply
