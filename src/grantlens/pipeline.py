"""Question -> SQL -> rows -> natural-language answer.

v1: schema in the prompt, one LLM call to write SQL, run it read-only, one LLM call to summarize.
No retrieval, validation or repair yet; a failure is reported as-is.

Try it: uv run python -m grantlens.pipeline "How many users are terminated?"
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from . import llm
from .db import connect_ro

SCHEMA_SQL = (Path(__file__).parent / "data" / "schema.sql").read_text()
TODAY = "2026-11-01"  # matches the seed data
MAX_ROWS = 200  # rows returned to the caller
SUMMARY_ROWS = 50  # rows shown to the LLM when summarizing
QUERY_TIMEOUT_S = 5.0

SQL_SYSTEM = f"""You translate questions about an identity and access governance database into one SQLite query.

Database schema:
{SCHEMA_SQL}
Rules:
- Write a single read-only SELECT statement in SQLite dialect. Use only the tables and columns above.
- Boolean columns hold 0 or 1. Dates are ISO-8601 text, so compare them as strings or with date().
- Treat today as {TODAY}. Use date('{TODAY}', '-90 days') for relative dates, never date('now').
- "Admin" access means entitlements.is_admin = 1. Access a user holds is in access_grants.
- Select human-readable columns (names, emails) rather than only ids.
Respond with JSON: {{"sql": "<the query>"}}"""

SQL_SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}

ANSWER_SYSTEM = """You answer a user's question using only the query results provided.
Be concise and concrete. Quote names and numbers exactly as they appear. List the items when there are only a few.
If a note says the results were truncated or partial, say so. Do not mention SQL unless the user asked about it."""


def _strip_fences(sql: str) -> str:
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1] if "\n" in sql else ""
        sql = sql.rsplit("```", 1)[0]
    return sql.strip().rstrip(";").strip()


async def generate_sql(question: str) -> str:
    raw = await llm.chat(
        [{"role": "system", "content": SQL_SYSTEM}, {"role": "user", "content": question}], schema=SQL_SCHEMA
    )
    try:
        return _strip_fences(json.loads(raw)["sql"])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise llm.LLMError(f"LLM did not return the expected JSON: {raw[:200]!r}") from e


def execute(sql: str) -> dict[str, Any]:
    """Run `sql` on a read-only connection with a time limit and row cap."""
    con = connect_ro()
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(MAX_ROWS + 1)]
    finally:
        con.close()
    truncated = len(rows) > MAX_ROWS
    return {"columns": columns, "rows": rows[:MAX_ROWS], "row_count": len(rows[:MAX_ROWS]), "truncated": truncated}


async def summarize(question: str, sql: str, result: dict[str, Any]) -> str:
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


async def ask(question: str) -> dict[str, Any]:
    """Answer `question` from the database. Never raises for expected failures; see the `error` key."""
    out: dict[str, Any] = {"question": question, "answer": "", "sql": None}
    t0 = time.monotonic()
    try:
        out["sql"] = await generate_sql(question)
        out["timings_ms"] = {"generate_sql": round((time.monotonic() - t0) * 1000)}
        t1 = time.monotonic()
        result = await asyncio.to_thread(execute, out["sql"])
        out["timings_ms"]["execute"] = round((time.monotonic() - t1) * 1000)
        out.update(result)
        t2 = time.monotonic()
        out["answer"] = await summarize(question, out["sql"], result)
        out["timings_ms"]["summarize"] = round((time.monotonic() - t2) * 1000)
    except llm.LLMError as e:
        out["error"] = str(e)
        out["answer"] = f"I couldn't reach the language model: {e}"
    except (sqlite3.Error, FileNotFoundError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["answer"] = "I generated a query but it failed to run, so I can't answer that yet."
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: python -m grantlens.pipeline "your question"')
    print(json.dumps(asyncio.run(ask(" ".join(sys.argv[1:]))), indent=2, default=str))
