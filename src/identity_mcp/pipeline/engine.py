"""``ask``: the whole flow for one question, tying the layers together.

follow-up rewrite (if there is history) -> retrieve examples -> write SQL -> check and repair -> run -> write the answer.
Expected failures never raise; they come back in the result's ``error`` key.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from ..context import conversation, handoff
from ..core import llm
from ..core.logs import flow, new_request_id, one_line
from ..guardrails import intent
from ..guardrails.scopes import Scope
from ..guardrails.validation import ValidationError
from ..rag import bank, retrieval
from .answer import summarize
from .executor import QueryError, execute
from .sql import produce_sql

def _outside_scope(scope: Scope | None) -> frozenset[str]:
    """Bank ids whose query reads a table outside `scope`; such examples are hidden from retrieval for that scope."""
    if scope is None:
        return frozenset()
    return frozenset(e.id for e in bank.load() if not scope.allows(e.sql))


async def ask(
    question: str,
    *,
    history: list[conversation.Turn] | None = None,
    scope: Scope | None = None,
    inputs: handoff.Input | None = None,
    summarize_answer: bool = True,
    k: int | None = None,
    exclude: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Answer `question` from the database. Never raises for expected failures; see the `error` key.

    `history` holds the earlier turns of the conversation, oldest first. When present, a follow-up such as "tell me
    more about him" is first rewritten into a standalone question (see ``conversation.py``); that rewritten text,
    returned as `standalone_question`, is what the rest of the pipeline runs on. `question` in the result is always
    what the user typed.

    `scope` restricts the question to one tool's tables (``scopes.py``): the prompt shows only those tables, only bank
    examples that read only those tables are retrieved, and the query is rejected if it touches anything else. `inputs`
    are ids handed over from an earlier tool call, exposed to the query as the ``input`` table (``handoff.py``).
    With `summarize_answer` False no plain-English answer is written (`answer` stays empty): a calling agent that
    reasons over the rows itself does not need one, and the step is the slowest model call.

    A request to change data ("delete all admins") is not run: it comes back at once with `refused` True and the read-only
    message as the `answer` (see ``guardrails/intent.py``). That is a reply, not an error, so `error` is not set.

    `k` overrides how many bank examples are retrieved (0 disables retrieval); `exclude` hides bank ids
    (used by evals to hold questions out).
    """
    rid = new_request_id()
    started = time.monotonic()
    flow.info("ask start id=%s k=%s excluded=%s question=%r", rid, "default" if k is None else k, sorted(exclude), question)
    out: dict[str, Any] = {"question": question, "answer": "", "sql": None, "examples": []}
    if scope is not None:
        out["scope"] = scope.name
    if inputs is not None:
        out["input_count"] = inputs.count
    timings: dict[str, int] = {}
    out["timings_ms"] = timings
    refusal = intent.check(question)  # a request to change data is answered at once: no retrieval, no model, no query
    if refusal is not None:
        flow.warning("refused a request to %s (read-only): %r", refusal.verb, question)
        out.update(answer=refusal.message, refused=True)
        return out
    try:
        if history:  # a follow-up is made standalone first; everything below runs on the rewritten question
            t = time.monotonic()
            rewrite = await conversation.standalone_question(question, history)
            timings["rewrite"] = round((time.monotonic() - t) * 1000)
            if rewrite.warning:
                out["warning"] = rewrite.warning
            if rewrite.changed:
                out["standalone_question"] = rewrite.question
            question = rewrite.question

        t = time.monotonic()
        hits: list[retrieval.Hit] = []
        try:
            hits = await retrieval.retrieve(question, k=k, exclude=exclude | _outside_scope(scope))
        except (llm.LLMError, FileNotFoundError) as e:  # degrade to no examples, but say so
            out["warning"] = (out["warning"] + " " if "warning" in out else "") + f"Answered without example retrieval: {e}"
            flow.warning("retrieval failed, continuing without examples: %s", e)
        out["examples"] = [{"id": h.example.id, "question": h.example.question, "score": round(h.score, 3)} for h in hits]
        timings["retrieve"] = round((time.monotonic() - t) * 1000)
        flow.info(
            "retrieved %d example(s) in %d ms: %s",
            len(hits), timings["retrieve"], "; ".join(f"{h.example.id} {h.score:.3f} {h.example.question!r}" for h in hits) or "none",
        )

        draft = await produce_sql(question, hits, scope, inputs)
        sql, check, attempts = draft.sql, draft.check, draft.attempts
        timings["generate_sql"] = draft.generate_ms
        out["sql"] = sql
        out["attempts"] = attempts
        out["repairs"] = draft.repairs
        timings["validate"] = draft.validate_ms
        if draft.repairs:
            timings["repair"] = draft.repair_ms
        if not check.ok:  # nothing is executed
            out["validation_errors"] = check.errors
            out["error"] = f"Query failed validation ({check.stage}): " + " ".join(check.errors)
            tried = f" after {out['repairs']} repair attempt(s)" if out["repairs"] else ""
            if check.stage == "grounding":
                intro = f"I couldn't find some of what you asked about in the data{tried}, so I didn't run anything:"
            else:
                intro = f"I couldn't produce a valid query for that question{tried}, so I didn't run anything. Problems found:"
            out["answer"] = intro + "\n" + "\n".join(f"- {e}" for e in check.errors)
        else:
            t = time.monotonic()
            result = await asyncio.to_thread(execute, out["sql"], scope, inputs)
            timings["execute"] = round((time.monotonic() - t) * 1000)
            out.update(result)
            flow.info(
                "executed in %d ms: %d row(s)%s columns=%s first_row=%s",
                timings["execute"], result["row_count"], " (truncated)" if result["truncated"] else "",
                result["columns"], one_line(result["rows"][0], 200) if result["rows"] else "-",
            )
            if summarize_answer:
                t = time.monotonic()
                out["answer"] = await summarize(question, out["sql"], result)
                timings["summarize"] = round((time.monotonic() - t) * 1000)
                flow.info("summarized in %d ms: %s", timings["summarize"], one_line(out["answer"], 300))
    except llm.LLMError as e:
        out["error"] = str(e)
        out["answer"] = f"I couldn't reach the language model: {e}"
    except (QueryError, ValidationError) as e:
        out["error"] = str(e)
        out["answer"] = f"The query couldn't be completed: {e}"
    except (sqlite3.Error, FileNotFoundError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["answer"] = "I generated a query but it failed to run, so I can't answer that yet."
    total = round((time.monotonic() - started) * 1000)
    if "error" in out:
        flow.error("ask failed after %d ms: %s | sql=%s", total, out["error"], one_line(out["sql"] or "-", 300))
    else:
        flow.info("ask done in %d ms", total)
    return out
