"""Loader for the question bank (examples/examples.md)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "examples" / "examples.md"

_ENTRY = re.compile(
    r"^## (?P<id>ex-\d+)\n\*\*Q:\*\* (?P<q>[^\n]+)\n\*\*Tags:\*\* (?P<tags>[^\n]+)\n\n```sql\n(?P<sql>(?s:.+?))\n```"
    r"(?:\n\n\*\*Notes:\*\* (?P<notes>(?s:.+?)))?(?=\n\n## ex-|\s*\Z)",
    re.M,
)


@dataclass(frozen=True)
class Example:
    """One curated question/answer pair from the question bank.

    Attributes:
        id: Stable identifier such as ``ex-058``; used to hold entries out of retrieval in evals.
        question: The English question, which is what gets embedded and matched against user questions.
        tags: Free-form labels from the ``**Tags:**`` line (e.g. ``terminated``, ``admin``, ``empty-ok``).
        sql: The expected SQLite query for the question, shown to the model as a worked example.
        notes: Optional ``**Notes:**`` text explaining a convention the query follows; shown with the example.
    """
    id: str
    question: str
    tags: tuple[str, ...]
    sql: str
    notes: str | None = None


def bank_path() -> Path:
    """Where the bank file lives: ``$IDENTITY_MCP_BANK`` if set, else ``examples/examples.md`` in the repo."""
    return Path(os.environ.get("IDENTITY_MCP_BANK", DEFAULT_PATH))


def parse(text: str) -> list[Example]:
    """Parse the text of a bank file into examples.

    Args:
        text: Markdown in the documented entry format (``## ex-NNN``, ``**Q:**``, ``**Tags:**``, a ```sql`` block,
            optional ``**Notes:**``).

    Returns:
        The entries in file order. Text that does not match the entry format is silently skipped, which is why
        ``tests/test_examples.py`` checks the parsed count against the number of ``## ex-`` headings.
    """
    return [
        Example(m["id"], m["q"], tuple(t.strip() for t in m["tags"].split(",")), m["sql"], m["notes"])
        for m in _ENTRY.finditer(text)
    ]


def load(path: Path | None = None) -> list[Example]:
    """Read and parse the bank file.

    Args:
        path: File to read; defaults to ``bank_path()``.

    Returns:
        The parsed examples. Raises ``FileNotFoundError`` if the file does not exist.
    """
    path = path or bank_path()
    if not path.exists():
        raise FileNotFoundError(f"question bank not found: {path}")
    return parse(path.read_text())
