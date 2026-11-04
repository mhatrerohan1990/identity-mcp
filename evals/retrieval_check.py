"""Two cheap checks of retrieval quality. Both need only the embedding model, never the chat model.

    uv run python -m evals.retrieval_check                       # both checks
    uv run python -m evals.retrieval_check --k 2

Each paraphrase case in ``cases.json`` was written from one bank entry (its "twin").

**Source-entry check.** The twin is *not* hidden: does it come back, and how high? Reported as **recall@k** (share of
cases whose twin is in the top k) and **MRR** (mean of 1/rank of the twin; 1.0 = always first, 0 = not in the top 10).
This is easy, because the question is a reworded copy of the twin.

**Held-out check.** The twin *is* hidden, as in the SQL evals, so the model has to learn from its neighbours. Here
we ask whether the neighbours look like the right answer, using the twin's SQL and tags as the stand-in:
**same tables** (share of cases where at least one retrieved example uses exactly the same set of tables as the
twin), **table overlap** and **tag overlap** (mean Jaccard similarity of the retrieved examples with the twin).
It is a proxy for "will these examples help the model write the right query"; only the full eval measures that.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass

from identity_mcp.rag import bank, retrieval
from identity_mcp.guardrails.validation import SCHEMA

from . import cases as cases_mod

DEPTH = 10  # how far down the ranking to look for the twin


@dataclass(frozen=True)
class Score:
    """Retrieval quality over the paraphrase cases."""

    cases: int
    recall: float
    mrr: float
    k: int


async def evaluate(k: int = 4) -> Score:
    """Score retrieval on every paraphrase case with the source entry visible.

    Uses ``min_score=0`` so the embedding threshold does not hide the comparison; only the ranking is judged.
    """
    paraphrases = [c for c in cases_mod.load() if c.kind == "paraphrase"]
    found = 0
    reciprocal = 0.0
    for case in paraphrases:
        (twin,) = case.hold_out
        ranked = [h.example.id for h in await retrieval.retrieve(case.question, k=DEPTH, min_score=0.0)]
        if twin in ranked[:k]:
            found += 1
        if twin in ranked:
            reciprocal += 1.0 / (ranked.index(twin) + 1)
    n = len(paraphrases)
    return Score(n, found / n, reciprocal / n, k)


@dataclass(frozen=True)
class HeldOut:
    """Neighbour quality when each paraphrase's own entry is hidden.

    Attributes:
        cases: Number of paraphrase cases.
        same_tables: Share of cases where at least one of the top k examples uses exactly the twin's tables.
        table_overlap: Mean Jaccard similarity between the examples' tables and the twin's.
        tag_overlap: Mean Jaccard similarity between the examples' tags and the twin's.
        k: How many examples were retrieved per case.
    """

    cases: int
    same_tables: float
    table_overlap: float
    tag_overlap: float
    k: int


def tables_in(sql: str) -> frozenset[str]:
    """The schema tables a query mentions, found by whole-word match against the table names."""
    return frozenset(t for t in SCHEMA if re.search(rf"\b{re.escape(t)}\b", sql, re.IGNORECASE))


def jaccard(a: frozenset | set, b: frozenset | set) -> float:
    """Size of the intersection over the size of the union; 1.0 for two empty sets."""
    return len(a & b) / len(a | b) if a | b else 1.0


async def evaluate_held_out(k: int = 4) -> HeldOut:
    """Score the neighbours retrieval returns for each paraphrase case with its source entry hidden."""
    by_id = {e.id: e for e in bank.load()}
    paraphrases = [c for c in cases_mod.load() if c.kind == "paraphrase"]
    same = 0
    table_sum = tag_sum = 0.0
    for case in paraphrases:
        (twin_id,) = case.hold_out
        twin = by_id[twin_id]
        want_tables, want_tags = tables_in(twin.sql), set(twin.tags)
        hits = await retrieval.retrieve(case.question, k=k, min_score=0.0, exclude=case.hold_out)
        if not hits:
            continue
        tables = [tables_in(h.example.sql) for h in hits]
        same += any(t == want_tables for t in tables)
        table_sum += sum(jaccard(t, want_tables) for t in tables) / len(hits)
        tag_sum += sum(jaccard(set(h.example.tags), want_tags) for h in hits) / len(hits)
    n = len(paraphrases)
    return HeldOut(n, same / n, table_sum / n, tag_sum / n, k)


async def main(k: int) -> None:
    """Run both checks and print the results."""
    s = await evaluate(k)
    print(f"source entry not hidden: cases={s.cases}  recall@{k}={s.recall:.2f}  MRR={s.mrr:.2f}")
    h = await evaluate_held_out(k)
    print(
        f"source entry hidden:     cases={h.cases}  same tables={h.same_tables:.2f}  "
        f"table overlap={h.table_overlap:.2f}  tag overlap={h.tag_overlap:.2f}"
    )


def cli() -> None:
    """Command-line entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--k", type=int, default=4, help="size of the top-k")
    asyncio.run(main(p.parse_args().k))


if __name__ == "__main__":
    cli()
