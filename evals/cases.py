"""Eval cases (evals/cases.json) and their gold SQL.

kind "paraphrase": a reworded bank question. `bank` names the entry whose SQL is the gold answer; that entry is
                    hidden from retrieval, so the model has to generalise from the *other* examples.
kind "novel":       a question with no bank twin and its own gold `sql`.
kind "absent":      asks about a value that is not in the data (a department or application that does not exist). Pass =
                    the pipeline says the value is missing, or runs and honestly finds nothing; fail = it swaps in a
                    different value and returns rows.
kind "safety":      a request to modify data, read internals or otherwise misbehave; there is no gold answer,
                    the check is that nothing changes and nothing unsafe runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from identity_mcp.rag import bank

CASES_PATH = Path(__file__).with_name("cases.json")


@dataclass(frozen=True)
class Case:
    """One eval question.

    Attributes:
        id: Case id such as ``p-058``.
        kind: ``paraphrase``, ``novel``, ``absent`` or ``safety`` (see the module docstring).
        question: What the user asks.
        gold_sql: Query whose rows are the correct answer; None for absent and safety cases.
        hold_out: Bank ids hidden from retrieval for this case (the entry the paraphrase came from).
    """
    id: str
    kind: str
    question: str
    gold_sql: str | None = None
    hold_out: frozenset[str] = frozenset()


def load(path: Path = CASES_PATH) -> list[Case]:
    """Read `cases.json` and attach gold SQL: for paraphrases it comes from the bank entry named in ``bank``, for novel cases from the inline ``sql``.
    """
    by_id = {e.id: e for e in bank.load()}
    cases = []
    for raw in json.loads(path.read_text()):
        kind = raw["kind"]
        if kind == "paraphrase":
            entry = by_id[raw["bank"]]
            cases.append(Case(raw["id"], kind, raw["question"], entry.sql, frozenset({entry.id})))
        elif kind == "novel":
            cases.append(Case(raw["id"], kind, raw["question"], raw["sql"]))
        elif kind in ("safety", "absent"):
            cases.append(Case(raw["id"], kind, raw["question"]))
        else:
            raise ValueError(f"{raw['id']}: unknown kind {kind!r}")
    return cases
