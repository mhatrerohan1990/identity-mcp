"""Writing the plain-English answer from a query result."""

from __future__ import annotations

import json
from typing import Any

from ..core import llm
from .executor import MAX_ROWS
from .prompts import ANSWER_SYSTEM

SUMMARY_ROWS = 50  # rows shown to the LLM when summarizing


async def summarize(question: str, sql: str, result: dict[str, Any]) -> str:
    """Write the plain-English answer for `question` from the query `result`.

    With zero rows no model call is made and a fixed "No matching records were found." is returned (small models
    misreport empty results). Otherwise at most ``SUMMARY_ROWS`` rows go to the model, with a note when the list was
    capped or truncated so the answer can say so.
    """
    if not result["rows"]:  # handled here: small models misreport emptiness when told how to word it
        return "No matching records were found."
    shown = result["rows"][:SUMMARY_ROWS]
    table = json.dumps({"columns": result["columns"], "rows": shown}, default=str)
    note = ""
    if result["truncated"]:
        note = f"\n(Results were capped at {MAX_ROWS} rows; there are more.)"
    elif len(result["rows"]) > len(shown):
        note = f"\n(Only the first {len(shown)} of {result['row_count']} rows are shown here.)"
    user = f"Question: {question}\n\nQuery results ({result['row_count']} rows):\n{table}{note}"
    return (await llm.chat([{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": user}])).strip()
