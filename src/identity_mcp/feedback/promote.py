"""Turn reviewed feedback into a bank example or an eval case, after checking it is safe and useful.

Two routes:

* **Thumbs up -> question bank.** A step whose SQL passed validation and ran is a good worked example for the retriever:
  the next similar question will be shown it. ``check_step`` decides whether a step qualifies and ``add_to_bank`` writes it.
* **Thumbs down -> eval case.** The failure is only useful as a test once someone supplies the *correct* SQL, so the
  reviewer types it; it is validated and run, then ``add_eval_case`` writes it as a case that must pass from then on.

Both writers refuse a question that is already there, and both re-run the SQL, so what lands in the files is known to work
against the current data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..guardrails.scopes import ACTIVITY, IDENTITY, Scope
from ..guardrails.validation import ValidationError, validate
from ..pipeline.executor import QueryError, execute
from ..rag import bank
from .store import Step

SCOPES = {"query_identity": IDENTITY, "query_activity": ACTIVITY}  # the single-query pipeline ("sql") is unscoped
CASES_PATH = Path(__file__).resolve().parents[3] / "evals" / "cases.json"


@dataclass
class Check:
    """Whether a step can be promoted, and why not if it cannot.

    Attributes:
        ok: True when the step may go into the bank.
        reasons: Plain-language reasons it cannot (empty when ok).
        row_count: Rows the query returned when it was re-run (0 if it never ran).
    """
    ok: bool
    reasons: list[str] = field(default_factory=list)
    row_count: int = 0


def normalise(question: str) -> str:
    """A question reduced to lower-case words, so "Who is Arjun?" and "who is arjun" count as the same."""
    return " ".join(re.findall(r"[a-z0-9]+", question.lower()))


def scope_for(tool: str) -> Scope | None:
    """The table scope a tool runs under, or None for the single-query pipeline."""
    return SCOPES.get(tool)


def run_check(sql: str, scope: Scope | None) -> Check:
    """Validate `sql` for `scope` and run it on the current data; report whether it works and how many rows it returns."""
    if not sql.strip():
        return Check(False, ["there is no SQL"])
    verdict = validate(sql, scope=scope)
    if not verdict.ok:
        return Check(False, [f"SQL did not pass validation ({verdict.stage}): {' '.join(verdict.errors)}"])
    try:
        return Check(True, row_count=execute(sql, scope)["row_count"])
    except (QueryError, ValidationError) as e:
        return Check(False, [f"SQL did not run: {e}"])


def check_step(step: Step, entries: list[bank.Example] | None = None) -> Check:
    """Whether `step` can become a bank example.

    It must not depend on an earlier step, must have run without error, must pass validation and run now, and its question
    must not already be in the bank.
    """
    entries = bank.load() if entries is None else entries
    reasons = []
    if step.linked:
        reasons.append("it used the result of an earlier step, so it only makes sense together with that step")
    if step.error:
        reasons.append(f"the step ended with an error: {step.error}")
    if any(normalise(e.question) == normalise(step.question) for e in entries):
        reasons.append("the bank already has this question")
    if reasons:
        return Check(False, reasons)
    return run_check(step.sql, scope_for(step.tool))


def next_bank_id(entries: list[bank.Example]) -> str:
    """The next free bank id after the highest one in use, e.g. ``ex-105``."""
    return f"ex-{max((int(e.id.split('-')[1]) for e in entries), default=0) + 1:03d}"


def bank_entry(entry_id: str, question: str, sql: str, tags: list[str]) -> str:
    """One bank entry as markdown, in the format ``rag/bank.py`` parses."""
    return f"## {entry_id}\n**Q:** {' '.join(question.split())}\n**Tags:** {', '.join(tags)}\n\n```sql\n{sql.strip()}\n```\n"


def add_to_bank(step: Step, path: Path | None = None) -> str:
    """Append `step` to the question bank file and return the new entry's id.

    Raises:
        ValueError: if the step does not pass :func:`check_step`. Nothing is written in that case.
    """
    path = path or bank.bank_path()
    entries = bank.load(path)
    check = check_step(step, entries)
    if not check.ok:
        raise ValueError("; ".join(check.reasons))
    entry_id = next_bank_id(entries)
    tags = ["feedback", "identity" if step.tool == "query_identity" else "activity" if step.tool == "query_activity" else "single"]
    text = path.read_text().rstrip("\n")
    path.write_text(f"{text}\n\n{bank_entry(entry_id, step.question, step.sql, tags)}")
    return entry_id


def add_eval_case(question: str, gold_sql: str, scope: Scope | None = None, path: Path | None = None) -> str:
    """Add a ``novel`` eval case with `gold_sql` as its answer and return the case id (``f-01``, ``f-02``, ...).

    Raises:
        ValueError: if the SQL does not validate and run, or a case with the same question exists. Nothing is written then.
    """
    path = path or CASES_PATH
    cases = json.loads(path.read_text())
    if any(normalise(c["question"]) == normalise(question) for c in cases):
        raise ValueError("the eval set already has this question")
    check = run_check(gold_sql, scope)
    if not check.ok:
        raise ValueError("; ".join(check.reasons))
    used = [int(c["id"][2:]) for c in cases if re.fullmatch(r"f-\d+", c["id"])]
    case_id = f"f-{max(used, default=0) + 1:02d}"
    cases.append({"id": case_id, "kind": "novel", "question": question.strip(), "sql": gold_sql.strip()})
    path.write_text(json.dumps(cases, indent=1))  # the file is written with indent=1 and no final newline; keep it so diffs stay small
    return case_id
