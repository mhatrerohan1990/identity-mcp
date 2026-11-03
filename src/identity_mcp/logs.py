"""Logging for the ask flow.

Two loggers, both under `identity_mcp`:
  identity_mcp.flow    one line per pipeline step (question, retrieval, SQL, execution, answer, timings)
  identity_mcp.prompt  the full messages sent to the LLM and its raw replies (large)

Every record carries the request id of the `ask` call that produced it.
Environment: IDENTITY_MCP_LOG_LEVEL (default INFO) for the flow, IDENTITY_MCP_LOG_PROMPTS (default 1; 0 hides prompts).
Logs go to stderr, which keeps them out of the MCP stdio protocol on stdout.
"""

from __future__ import annotations

import contextvars
import logging
import os
import uuid

flow = logging.getLogger("identity_mcp.flow")
prompt = logging.getLogger("identity_mcp.prompt")

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:8]
    _request_id.set(rid)
    return rid


class _Context(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.component = record.name.rsplit(".", 1)[-1]
        return True


def one_line(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def block(title: str, body: str) -> str:
    return f"===== {title} =====\n{body}\n===== end {title} ====="


def configure() -> None:
    """Install the stderr handler and levels from the environment. Safe to call more than once."""
    root = logging.getLogger("identity_mcp")
    if not any(getattr(h, "_identity_mcp", False) for h in root.handlers):
        handler = logging.StreamHandler()  # stderr
        handler._identity_mcp = True  # type: ignore[attr-defined]
        handler.addFilter(_Context())
        handler.setFormatter(
            logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s [%(request_id)s] %(component)s | %(message)s", "%H:%M:%S")
        )
        root.addHandler(handler)
    level = getattr(logging, os.environ.get("IDENTITY_MCP_LOG_LEVEL", "INFO").upper(), None)
    flow.setLevel(level if isinstance(level, int) else logging.INFO)
    show_prompts = os.environ.get("IDENTITY_MCP_LOG_PROMPTS", "1").lower() not in ("0", "false", "no", "off")
    prompt.setLevel(logging.DEBUG if show_prompts else logging.WARNING)
