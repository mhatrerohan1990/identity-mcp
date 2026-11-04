"""Eval cases (evals/cases.json) and their gold SQL.

kind "paraphrase": a reworded bank question. `bank` names the entry whose SQL is the gold answer; that entry is
                    hidden from retrieval, so the model has to generalise from the *other* examples.
kind "novel":       a question with no bank twin and its own gold `sql`.
kind "safety":      a request to modify data, read internals or otherwise misbehave; there is no gold answer,
                    the check is that nothing changes and nothing unsafe runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from identity_mcp import bank

CASES_PATH = Path(__file__).with_name("cases.json")


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    question: str
    gold_sql: str | None = None
    hold_out: frozenset[str] = frozenset()


def load(path: Path = CASES_PATH) -> list[Case]:
    by_id = {e.id: e for e in bank.load()}
    cases = []
    for raw in json.loads(path.read_text()):
        kind = raw["kind"]
        if kind == "paraphrase":
            entry = by_id[raw["bank"]]
            cases.append(Case(raw["id"], kind, raw["question"], entry.sql, frozenset({entry.id})))
        elif kind == "novel":
            cases.append(Case(raw["id"], kind, raw["question"], raw["sql"]))
        elif kind == "safety":
            cases.append(Case(raw["id"], kind, raw["question"]))
        else:
            raise ValueError(f"{raw['id']}: unknown kind {kind!r}")
    return cases
