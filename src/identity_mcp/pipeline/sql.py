"""Writing the SQL: first attempt, checking, and the repair loop.

``produce_sql`` asks the model for a query, runs every pre-execution check on it (``check_sql``: validation, then value
grounding) and, when a check fails, sends the query back with the reasons for up to ``max_repairs()`` more attempts. It
never executes anything.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from ..context import handoff
from ..core import llm
from ..core.logs import flow, one_line
from ..guardrails import grounding
from ..guardrails.scopes import Scope
from ..guardrails.validation import Validation, validate
from ..rag import retrieval
from .prompts import REPAIR_FEEDBACK, SQL_SCHEMA, sql_messages

DEFAULT_MAX_REPAIRS = 2  # extra model attempts after a query fails validation


def _strip_fences(sql: str) -> str:
    """Clean model output into a bare statement: remove a Markdown code fence if present, then trailing semicolons and whitespace.
    """
    sql = sql.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1] if "\n" in sql else ""
        sql = sql.rsplit("```", 1)[0]
    return sql.strip().rstrip(";").strip()


def max_repairs() -> int:
    """How many times a failed query may be sent back to the model: ``$IDENTITY_MCP_MAX_REPAIRS``, default 2, never below 0.
    """
    try:
        return max(0, int(os.environ.get("IDENTITY_MCP_MAX_REPAIRS", DEFAULT_MAX_REPAIRS)))
    except ValueError:
        return DEFAULT_MAX_REPAIRS


async def _request_sql(messages: list[dict[str, str]]) -> str:
    """Send `messages` to the model with the JSON schema ``{"sql": ...}`` and return the cleaned query.

    Raises:
        llm.LLMError: if the reply is not the expected JSON.
    """
    raw = await llm.chat(messages, schema=SQL_SCHEMA)
    try:
        return _strip_fences(json.loads(raw)["sql"])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise llm.LLMError(f"LLM did not return the expected JSON: {raw[:200]!r}") from e


async def generate_sql(
    question: str,
    hits: list[retrieval.Hit] | None = None,
    scope: Scope | None = None,
    inputs: handoff.Input | None = None,
) -> str:
    """First attempt at turning `question` into SQL, using the retrieved `hits` as worked examples. No checking happens here.
    """
    return await _request_sql(sql_messages(question, hits or [], scope, inputs))


async def repair_sql(
    question: str,
    hits: list[retrieval.Hit] | None,
    failed: list[tuple[str, list[str]]],
    scope: Scope | None = None,
    inputs: handoff.Input | None = None,
) -> str:
    """Ask again with the conversation so far: each failed query as the model's own reply, then why it failed."""
    messages = sql_messages(question, hits or [], scope, inputs)
    for sql, errors in failed:
        problems = "\n".join(f"- {e}" for e in errors)
        messages += [
            {"role": "assistant", "content": json.dumps({"sql": sql})},
            {"role": "user", "content": REPAIR_FEEDBACK.format(problems=problems)},
        ]
    return await _request_sql(messages)


def check_sql(sql: str, scope: Scope | None = None, inputs: handoff.Input | None = None) -> Validation:
    """Every pre-execution check, in order: well-formed and safe (validation), then filters on real values (grounding).

    The query is checked exactly as it will run, including the ``input`` table definition when `inputs` are given.
    """
    missing = handoff.unused_input(sql, inputs)
    if missing:
        return Validation(False, "input", missing)
    sql = handoff.with_input(sql, inputs)
    result = validate(sql, scope=scope)
    if result.ok:
        problems = grounding.check(sql)
        if problems:
            return Validation(False, "grounding", problems)
    return result


@dataclass
class Draft:
    """The outcome of generating SQL and validating/repairing it. `check.ok` says whether `sql` may be executed."""

    sql: str
    check: Validation
    attempts: list[dict[str, Any]]
    repairs: int
    generate_ms: int
    validate_ms: int
    repair_ms: int


async def produce_sql(
    question: str,
    hits: list[retrieval.Hit],
    scope: Scope | None = None,
    inputs: handoff.Input | None = None,
) -> Draft:
    """Generate SQL, validate it, and send failures back to the model for up to `max_repairs()` retries."""
    t = time.monotonic()
    sql = await generate_sql(question, hits, scope, inputs)
    generate_ms = round((time.monotonic() - t) * 1000)
    flow.info("generated sql in %d ms: %s", generate_ms, one_line(sql, 600))

    limit = max_repairs()
    attempts: list[dict[str, Any]] = []
    failed: list[tuple[str, list[str]]] = []
    validate_ms = repair_ms = repairs = 0
    while True:
        tv = time.monotonic()
        check = await asyncio.to_thread(check_sql, sql, scope, inputs)
        validate_ms += round((time.monotonic() - tv) * 1000)
        attempts.append({"sql": sql, "ok": check.ok, "stage": check.stage, "errors": check.errors})
        if check.ok:
            flow.info("validation passed (static + dry run) on attempt %d/%d", len(attempts), limit + 1)
            break
        flow.warning(
            "validation failed at %s stage on attempt %d/%d: %s", check.stage, len(attempts), limit + 1, " | ".join(check.errors)
        )
        failed.append((sql, check.errors))
        if len(failed) > limit:
            break
        tr = time.monotonic()
        flow.info("repair %d/%d: asking the model to fix its query", len(failed), limit)
        new_sql = await repair_sql(question, hits, failed, scope, inputs)
        repairs += 1
        repair_ms += round((time.monotonic() - tr) * 1000)
        flow.info("repair %d/%d produced: %s", len(failed), limit, one_line(new_sql, 600))
        if " ".join(new_sql.split()) == " ".join(sql.split()):
            flow.warning("the model returned the same query; not retrying further")
            break
        sql = new_sql
    return Draft(sql, check, attempts, repairs, generate_ms, validate_ms, repair_ms)
