"""Question -> SQL -> rows -> natural-language answer.

Schema + retrieved bank examples in the prompt, one LLM call to write SQL, run it read-only, one LLM call to
summarize. No validation or repair yet; a failure is reported as-is.

Try it: uv run python -m identity_mcp.pipeline "How many users are terminated?"
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from . import llm, retrieval
from .db import connect_ro
from .logs import configure, flow, new_request_id, one_line

SCHEMA_SQL = (Path(__file__).parent / "data" / "schema.sql").read_text()
TODAY = "2026-11-01"  # matches the seed data
MAX_ROWS = 200  # rows returned to the caller
SUMMARY_ROWS = 50  # rows shown to the LLM when summarizing
QUERY_TIMEOUT_S = 5.0

SQL_SYSTEM_BASE = f"""You translate questions about an identity and access governance database into one SQLite query.

Database schema:
{SCHEMA_SQL}
Rules:
- Write a single read-only SELECT statement in SQLite dialect. Use only the tables and columns above.
- Boolean columns hold 0 or 1. Dates are ISO-8601 text, so compare them as strings or with date().
- Treat today as {TODAY}. Use date('{TODAY}', '-90 days') for relative dates, never date('now').
- "Admin" access means entitlements.is_admin = 1. Access a user holds is in access_grants.
- Select human-readable columns (names, emails) rather than only ids.
Respond with JSON: {{"sql": "<the query>"}}"""

EXAMPLES_INTRO = """

Worked examples of similar questions with the correct query. Follow their patterns and conventions
(the closest example is last):
"""

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


def build_sql_system(hits: list[retrieval.Hit]) -> str:
    if not hits:
        return SQL_SYSTEM_BASE
    blocks = []
    for h in reversed(hits):  # best match last, nearest the question
        block = f"Question: {h.example.question}\nSQL: {h.example.sql}"
        if h.example.notes:
            block += f"\nNote: {h.example.notes}"
        blocks.append(block)
    return SQL_SYSTEM_BASE + EXAMPLES_INTRO + "\n\n".join(blocks)


async def generate_sql(question: str, hits: list[retrieval.Hit] | None = None) -> str:
    raw = await llm.chat(
        [{"role": "system", "content": build_sql_system(hits or [])}, {"role": "user", "content": question}],
        schema=SQL_SCHEMA,
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


async def ask(question: str, *, k: int | None = None, exclude: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Answer `question` from the database. Never raises for expected failures; see the `error` key.

    `k` overrides how many bank examples are retrieved (0 disables retrieval); `exclude` hides bank ids
    (used by evals to hold questions out).
    """
    rid = new_request_id()
    started = time.monotonic()
    flow.info("ask start id=%s k=%s excluded=%s question=%r", rid, "default" if k is None else k, sorted(exclude), question)
    out: dict[str, Any] = {"question": question, "answer": "", "sql": None, "examples": []}
    timings: dict[str, int] = {}
    out["timings_ms"] = timings
    try:
        t = time.monotonic()
        hits: list[retrieval.Hit] = []
        try:
            hits = await retrieval.retrieve(question, k=k, exclude=exclude)
        except (llm.LLMError, FileNotFoundError) as e:  # degrade to no examples, but say so
            out["warning"] = f"Answered without example retrieval: {e}"
            flow.warning("retrieval failed, continuing without examples: %s", e)
        out["examples"] = [{"id": h.example.id, "question": h.example.question, "score": round(h.score, 3)} for h in hits]
        timings["retrieve"] = round((time.monotonic() - t) * 1000)
        flow.info(
            "retrieved %d example(s) in %d ms: %s",
            len(hits), timings["retrieve"], "; ".join(f"{h.example.id} {h.score:.3f} {h.example.question!r}" for h in hits) or "none",
        )

        t = time.monotonic()
        out["sql"] = await generate_sql(question, hits)
        timings["generate_sql"] = round((time.monotonic() - t) * 1000)
        flow.info("generated sql in %d ms: %s", timings["generate_sql"], one_line(out["sql"], 600))
        t = time.monotonic()
        result = await asyncio.to_thread(execute, out["sql"])
        timings["execute"] = round((time.monotonic() - t) * 1000)
        out.update(result)
        flow.info(
            "executed in %d ms: %d row(s)%s columns=%s first_row=%s",
            timings["execute"], result["row_count"], " (truncated)" if result["truncated"] else "",
            result["columns"], one_line(result["rows"][0], 200) if result["rows"] else "-",
        )
        t = time.monotonic()
        out["answer"] = await summarize(question, out["sql"], result)
        timings["summarize"] = round((time.monotonic() - t) * 1000)
        flow.info("summarized in %d ms: %s", timings["summarize"], one_line(out["answer"], 300))
    except llm.LLMError as e:
        out["error"] = str(e)
        out["answer"] = f"I couldn't reach the language model: {e}"
    except (sqlite3.Error, FileNotFoundError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["answer"] = "I generated a query but it failed to run, so I can't answer that yet."
    total = round((time.monotonic() - started) * 1000)
    if "error" in out:
        flow.error("ask failed after %d ms: %s | sql=%s", total, out["error"], one_line(out["sql"] or "-", 300))
    else:
        flow.info("ask done in %d ms", total)
    return out


if __name__ == "__main__":
    configure()
    if len(sys.argv) < 2:
        sys.exit('usage: python -m identity_mcp.pipeline "your question"')
    print(json.dumps(asyncio.run(ask(" ".join(sys.argv[1:]))), indent=2, default=str))
