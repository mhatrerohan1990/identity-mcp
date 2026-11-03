"""Minimal async client for a local Ollama server."""

from __future__ import annotations

import os
from typing import Any

import httpx

BASE_URL = os.environ.get("IDENTITY_MCP_LLM_URL", "http://localhost:11434")
MODEL = os.environ.get("IDENTITY_MCP_LLM_MODEL", "llama3.1:8b")
EMBED_MODEL = os.environ.get("IDENTITY_MCP_EMBED_MODEL", "nomic-embed-text")


class LLMError(RuntimeError):
    pass


async def _post(path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{BASE_URL}{path}", json=body)
            r.raise_for_status()
    except httpx.ConnectError as e:
        raise LLMError(f"Cannot reach the LLM at {BASE_URL}. Is Ollama running?") from e
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise LLMError(f"Model {body.get('model')!r} not found. Run: ollama pull {body.get('model')}") from e
        raise LLMError(f"LLM request failed: {e}") from e
    except httpx.HTTPError as e:
        raise LLMError(f"LLM request failed: {e}") from e
    return r.json()


async def chat(messages: list[dict[str, str]], *, schema: dict[str, Any] | None = None, timeout: float = 120) -> str:
    """One non-streaming chat completion at temperature 0.

    With `schema`, Ollama constrains the output to that JSON schema.
    """
    body: dict[str, Any] = {"model": MODEL, "messages": messages, "stream": False, "options": {"temperature": 0}}
    if schema:
        body["format"] = schema
    return (await _post("/api/chat", body, timeout))["message"]["content"]


async def embed(texts: list[str], *, timeout: float = 60) -> list[list[float]]:
    """Embed `texts` with the embedding model (nomic-embed-text expects a task prefix on each text)."""
    return (await _post("/api/embed", {"model": EMBED_MODEL, "input": texts}, timeout))["embeddings"]
