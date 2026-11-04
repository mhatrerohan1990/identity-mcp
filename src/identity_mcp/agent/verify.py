"""Check an answer against the data it was written from. Plain code: no model call, so it is free, fast and repeatable.

The model reads tool results and writes a sentence about them, and small models make small mistakes there: a total that
was never in the data, a name that is not in any row, a list that quietly loses two of six rows. The SQL is already
validated; this checks the *wording* with three tests.

1. **Numbers.** Every number in the answer must appear in the results (a row count, a cell value, how many distinct values a
   column has) or in a question that was asked ("the last 90 days"). Dates and list numbering are ignored.
2. **Listed names.** Every bullet or numbered item that looks like a name must match something in the results.
3. **Omissions.** If the answer lists most of the last result's names but not all of them, the missing ones are reported.
   An answer that lists none of them is read as a summary ("15 accounts"), not a list, and is not flagged.

``verify_answer`` returns a list of plain-English problems, which is empty when the answer holds up. The same sentences
are shown to the model when it is asked to correct itself. Results the tool reported as errors are ignored.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
MARKER = re.compile(r"^\s*\d+[.)]\s+")  # "3. " at the start of a line is numbering, not a number the answer states
MIN_VALUE_LENGTH = 3  # shorter cell values ("A", "IT") match too much text to prove anything
MAX_ITEM_WORDS = 6  # a longer list item is a sentence, not a name
MAX_LISTED = 5  # at most this many problems of one kind are spelled out


def numbers_in(text: str) -> set[str]:
    """The numbers written in `text` as plain tokens (``"1,200"`` becomes ``"1200"``), ignoring dates and list numbering."""
    cleaned = DATE.sub(" ", "\n".join(MARKER.sub("", line) for line in text.splitlines()))
    return {m.group(0).replace(",", "").rstrip(".") for m in NUMBER.finditer(cleaned)}


def results_of(messages: list[AnyMessage]) -> list[dict[str, Any]]:
    """The successful tool results among `messages`, parsed, oldest first. Errors and non-JSON messages are skipped."""
    found = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            body = json.loads(message.content)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(body, dict) and not body.get("error") and isinstance(body.get("rows"), list):
            found.append(body)
    return found


def asked_in(messages: list[AnyMessage]) -> str:
    """Everything the user and the model asked, as one text: user turns plus the question given to each tool."""
    parts = [m.content for m in messages if isinstance(m, HumanMessage) and isinstance(m.content, str)]
    for message in messages:
        if isinstance(message, AIMessage):
            parts += [str(c.get("args", {}).get("question", "")) for c in message.tool_calls]
    return "\n".join(parts)


def allowed_numbers(results: list[dict[str, Any]], asked: str) -> set[str]:
    """Every number the answer may state: counts and cells from the results, and the numbers in the questions asked."""
    allowed = numbers_in(asked)
    for result in results:
        rows = result["rows"]
        allowed |= {str(result.get("row_count", len(rows))), str(len(rows))}
        for column in zip(*rows) if rows else []:
            allowed.add(str(len({str(v) for v in column})))  # "14 different people" is as true as "15 rows"
        for row in rows:
            for value in row:
                allowed |= numbers_in(str(value))
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    allowed.add(str(int(value)) if float(value).is_integer() else str(value))
    return allowed


def strings_in(results: list[dict[str, Any]]) -> set[str]:
    """Every text cell of the results, lower-cased, that is long enough to prove something."""
    return {str(v).lower() for r in results for row in r["rows"] for v in row
            if isinstance(v, str) and len(v) >= MIN_VALUE_LENGTH}


def list_items(answer: str) -> list[str]:
    """The text of each bullet or numbered line of `answer`, without markdown emphasis."""
    items = []
    for line in answer.splitlines():
        match = LIST_ITEM.match(line)
        if match:
            items.append(match.group(1).replace("**", "").replace("__", "").strip())
    return items


def looks_like_name(item: str) -> bool:
    """Whether a list item is short and capitalised like a name or title, not a sentence: only those can be checked."""
    words = item.split()
    return 0 < len(words) <= MAX_ITEM_WORDS and item[0].isupper() and ":" not in item and not item.endswith((".", "?"))


def name_column(result: dict[str, Any]) -> int | None:
    """Index of the column that names the rows: ``name`` if present, else the first text column that is not an email."""
    columns, rows = result.get("columns", []), result["rows"]
    if "name" in columns:
        return columns.index("name")
    for i in range(len(columns)):
        values = [row[i] for row in rows]
        if values and all(isinstance(v, str) and "@" not in v for v in values):
            return i
    return None


def omitted(answer: str, result: dict[str, Any]) -> tuple[list[str], int]:
    """Names from `result` that the answer leaves out while listing most of the others.

    Returns:
        ``(missing, total)``: the missing names and how many distinct names the result has. ``missing`` is empty when the
        result was cut off (some rows were never shown), has no name column, or the answer names fewer than half of them.
    """
    if result.get("truncated") or result.get("row_count", 0) > len(result["rows"]):
        return [], 0
    index = name_column(result)
    if index is None:
        return [], 0
    names = list(dict.fromkeys(str(row[index]) for row in result["rows"]))
    lowered = answer.lower()
    mentioned = [n for n in names if n.lower() in lowered]
    missing = [n for n in names if n.lower() not in lowered]
    if not missing or len(mentioned) * 2 < len(names):
        return [], len(names)
    return missing, len(names)


def verify_answer(answer: str, results: list[dict[str, Any]], asked: str = "") -> list[str]:
    """The problems found in `answer` given the tool `results` it was written from (empty list: it holds up).

    Args:
        answer: The text the model gave as its final answer.
        results: Parsed tool results from :func:`results_of`.
        asked: What was asked, from :func:`asked_in`; numbers in it (such as "90 days") count as supported.
    """
    if not results:
        return []
    problems = []
    allowed = allowed_numbers(results, asked)
    unsupported = sorted(numbers_in(answer) - allowed, key=lambda n: (len(n), n))
    for number in unsupported[:MAX_LISTED]:
        problems.append(f"The answer says {number}, but no result contains that number. Use only numbers from the results "
                        f"(row_count is the total); do not add up or estimate.")
    known = strings_in(results)
    for item in list_items(answer):
        lowered = item.lower()
        if looks_like_name(item) and not any(v in lowered or lowered in v for v in known):
            problems.append(f"The answer lists '{item}', which is not in any result.")
            if len(problems) >= MAX_LISTED * 2:
                break
    missing, total = omitted(answer, results[-1])
    if missing:
        shown = ", ".join(missing[:MAX_LISTED]) + (" and more" if len(missing) > MAX_LISTED else "")
        problems.append(f"The answer leaves out {len(missing)} of the {total} names in the last result: {shown}. "
                        f"List all of them, or give the count instead of a partial list.")
    return problems
