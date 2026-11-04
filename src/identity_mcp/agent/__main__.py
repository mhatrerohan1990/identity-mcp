"""Command line: ``uv run python -m identity_mcp.agent "show me dormant admin accounts"``.

Each step is printed to stderr as it happens (the tool the model chose, its arguments, what came back), and the final answer
goes to stdout. Needs Ollama running and ``uv sync --extra agent``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_ollama import ChatOllama

from ..core import llm
from ..core.logs import configure
from .graph import DEFAULT_MAX_STEPS, build_graph, run
from .tools import open_tools

MODEL_ENV = "IDENTITY_MCP_AGENT_MODEL"  # the agent's model; defaults to the same model as SQL generation


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


def make_printer(started: float):
    """A callback for :func:`graph.run` that prints one line per step to stderr, with elapsed seconds."""

    def show(node: str, update: dict[str, Any]) -> None:
        stamp = f"[{time.monotonic() - started:5.1f}s]"
        for message in update.get("messages", []):
            if isinstance(message, AIMessage) and message.tool_calls:
                for call in message.tool_calls:
                    print(f"{stamp} -> {describe_call(call)}", file=sys.stderr)
            elif isinstance(message, AIMessage):
                print(f"{stamp} model is answering", file=sys.stderr)
            elif isinstance(message, ToolMessage):
                print(f"{stamp} <- {describe_result(message)}", file=sys.stderr)
            elif node == "nudge":
                print(f"{stamp} (plain text is not an answer: told the model to call a tool or final_answer)", file=sys.stderr)

    return show


async def main(question: str, model_name: str, max_steps: int) -> str:
    """Connect to the server, run the agent on `question`, and return the answer."""
    model = ChatOllama(model=model_name, base_url=llm.BASE_URL, temperature=0)
    started = time.monotonic()
    async with open_tools() as tools:
        print(f"[ 0.0s] model {model_name}; tools: {', '.join(t.name for t in tools)}", file=sys.stderr)
        return await run(build_graph(model, tools, max_steps), question, make_printer(started))


def cli() -> None:
    """Parse the command line, run, and print the answer."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="+", help="what to ask, in plain English")
    parser.add_argument("--model", default=os.environ.get(MODEL_ENV, llm.MODEL), help=f"Ollama model (default ${MODEL_ENV} or the SQL model)")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="most model calls per question")
    args = parser.parse_args()
    configure()
    print(asyncio.run(main(" ".join(args.question), args.model, args.max_steps)))


if __name__ == "__main__":
    cli()
