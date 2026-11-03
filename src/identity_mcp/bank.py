"""Loader for the question bank (examples/examples.md)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "examples" / "examples.md"

_ENTRY = re.compile(
    r"^## (?P<id>ex-\d+)\n\*\*Q:\*\* (?P<q>[^\n]+)\n\*\*Tags:\*\* (?P<tags>[^\n]+)\n\n```sql\n(?P<sql>(?s:.+?))\n```"
    r"(?:\n\n\*\*Notes:\*\* (?P<notes>(?s:.+?)))?(?=\n\n## ex-|\s*\Z)",
    re.M,
)


@dataclass(frozen=True)
class Example:
    id: str
    question: str
    tags: tuple[str, ...]
    sql: str
    notes: str | None = None


def bank_path() -> Path:
    return Path(os.environ.get("IDENTITY_MCP_BANK", DEFAULT_PATH))


def parse(text: str) -> list[Example]:
    return [
        Example(m["id"], m["q"], tuple(t.strip() for t in m["tags"].split(",")), m["sql"], m["notes"])
        for m in _ENTRY.finditer(text)
    ]


def load(path: Path | None = None) -> list[Example]:
    path = path or bank_path()
    if not path.exists():
        raise FileNotFoundError(f"question bank not found: {path}")
    return parse(path.read_text())
