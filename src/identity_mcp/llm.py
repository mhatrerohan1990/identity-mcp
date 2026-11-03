"""Minimal async client for a local Ollama server."""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = os.environ.get("IDENTITY_MCP_LLM_URL", "http://localhost:11434")
MODEL = os.environ.get("IDENTITY_MCP_LLM_MODEL", "llama3.1:8b")


class LLMError(RuntimeError):
    pass


async def chat(messages: list[dict[str, str]], *, schema: dict[str, Any] | None = None, timeout: float = 120) -> str:
    """One non-streaming chat completion at temperature 0.

    With `schema`, Ollama constrains the output to that JSON schema.
    """
    body: dict[str, Any] = {"model": MODEL, "messages": messages, "stream": False, "options": {"temperature": 0}}
    if schema:
        body["format"] = schema
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{BASE_URL}/api/chat", json=body)
            r.raise_for_status()
    except httpx.ConnectError as e:
        raise LLMError(f"Cannot reach the LLM at {BASE_URL}. Is Ollama running?") from e
    except httpx.HTTPError as e:
        raise LLMError(f"LLM request failed: {e}") from e
    return r.json()["message"]["content"]
