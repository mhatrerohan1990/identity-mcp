"""Minimal async client for a local Ollama server."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from .logs import block, prompt
BASE_URL = os.environ.get("IDENTITY_MCP_LLM_URL", "http://localhost:11434")
MODEL = os.environ.get("IDENTITY_MCP_LLM_MODEL", "qwen2.5-coder:14b")
EMBED_MODEL = os.environ.get("IDENTITY_MCP_EMBED_MODEL", "nomic-embed-text")


class LLMError(RuntimeError):
    """The language model could not be reached or answered unusably (Ollama down, model not pulled, bad reply).

    The message is written for the end user, e.g. it includes the ``ollama pull`` command when a model is missing.
    """
    pass


async def _post(path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST `body` as JSON to the Ollama endpoint `path` and return the decoded reply.

    Raises:
        LLMError: when the server is unreachable (with a hint to start Ollama), when the model is not installed
            (404, with the ``ollama pull`` command), or on any other HTTP error.
    """
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
    if prompt.isEnabledFor(10):  # DEBUG: the exact messages the model receives
        shown = "\n\n".join(f"--- {m['role']} ---\n{m['content']}" for m in messages)
        prompt.debug("%s", block(f"LLM request model={MODEL} structured={'yes' if schema else 'no'}", shown))
    t = time.monotonic()
    content = (await _post("/api/chat", body, timeout))["message"]["content"]
    prompt.debug("%s", block(f"LLM response ({round((time.monotonic() - t) * 1000)} ms)", content))
    return content


async def embed(texts: list[str], *, timeout: float = 60) -> list[list[float]]:
    """Embed `texts` with the embedding model (nomic-embed-text expects a task prefix on each text)."""
    return (await _post("/api/embed", {"model": EMBED_MODEL, "input": texts}, timeout))["embeddings"]
