"""The agent's MCP client: turn the server's tools into tools LangChain can call.

Why a bridge of our own instead of ``langchain-mcp-adapters``: that package (0.3.1) imports a class that the MCP
SDK 2.x we use no longer has, so it fails at import. The bridge is small: list the server's tools, wrap each in a
``StructuredTool`` whose call goes through ``mcp.Client.call_tool``.

Two details matter for small local models:

* the server's JSON schemas use ``$defs``/``$ref`` and ``anyOf [x, null]`` for optional arguments; ``flatten_schema``
  inlines those, because models call tools far more reliably against a plain nested schema;
* a tool's result is handed back as JSON text, and a failed call becomes a message the model can read and react to, not an
  exception that ends the run.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.tools import StructuredTool, ToolException
from mcp import Client, StdioServerParameters

SERVER_ENV = "IDENTITY_MCP_AGENT_SERVER"  # a URL such as http://localhost:8000/mcp; unset launches the server over stdio


def flatten_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A simpler copy of a JSON schema: references inlined, optional (``anyOf`` with null) collapsed, titles dropped.

    Args:
        schema: A tool's ``input_schema`` as published by the MCP server.

    Returns:
        An equivalent schema without ``$defs``, ``$ref``, ``title`` or ``null`` alternatives, which small models handle badly.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                rest = {k: v for k, v in node.items() if k != "anyOf"}
                return walk({**options[0], **rest})
        out = {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if out.get("default") is None:
            out.pop("default", None)
        return out

    return walk(schema)


def prune(value: Any) -> Any:
    """`value` without the placeholders small models put in for "not given".

    Removes ``None`` values and empty or whitespace-only strings from dicts and lists, recursively, and then any dict or
    list left empty. A model asked for an optional object such as ``filter_from`` often sends
    ``{"result_id": null, "column": null}`` instead of leaving it out; pruned, it becomes "not given", as intended.
    """
    if isinstance(value, dict):
        pruned = {k: prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v is not None and v != {} and v != [] and v != ""}
    if isinstance(value, list):
        return [v for v in (prune(item) for item in value) if v is not None]
    if isinstance(value, str) and not value.strip():
        return None
    return value


def to_langchain_tool(client: Client, tool: Any) -> StructuredTool:
    """Wrap one MCP tool (from ``list_tools``) as a LangChain tool that calls it through `client`."""

    async def call(**arguments: Any) -> str:
        arguments = prune(arguments)
        result = await client.call_tool(tool.name, arguments)
        if result.structured_content is not None:
            text = json.dumps(result.structured_content, default=str)
        else:
            text = "\n".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            raise ToolException(text or f"{tool.name} failed")
        return text

    return StructuredTool(
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=flatten_schema(tool.input_schema),
        coroutine=call,
        handle_tool_error=True,
    )


def server_target() -> str | StdioServerParameters:
    """Where the MCP server is: the URL in ``$IDENTITY_MCP_AGENT_SERVER``, else a stdio subprocess launched on demand.

    The subprocess gets the ``IDENTITY_MCP_*`` settings (database path, model, ...) from this process's environment.
    """
    url = os.environ.get(SERVER_ENV)
    if url:
        return url
    env = {k: v for k, v in os.environ.items() if k.startswith("IDENTITY_MCP_") or k in ("PATH", "HOME", "LANG")}
    env.setdefault("IDENTITY_MCP_LOG_PROMPTS", "0")  # the server's own logs would otherwise flood this terminal
    env.setdefault("IDENTITY_MCP_LOG_LEVEL", "WARNING")
    return StdioServerParameters(command=sys.executable, args=["-m", "identity_mcp.mcp_server"], env=env)


@asynccontextmanager
async def open_tools(target: Any = None) -> AsyncIterator[list[StructuredTool]]:
    """Connect to the server and yield its tools as LangChain tools; the connection closes when the block exits.

    Args:
        target: Anything ``mcp.Client`` accepts (a URL, ``StdioServerParameters``, or in tests an in-process server).
            Defaults to ``server_target()``.
    """
    async with Client(target if target is not None else server_target()) as client:
        listed = (await client.list_tools()).tools
        yield [to_langchain_tool(client, t) for t in listed]
