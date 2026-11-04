"""Two cheap checks of retrieval quality. Both need only the embedding model, never the chat model.

    uv run python -m evals.retrieval_check                       # both checks, all three strategies
    uv run python -m evals.retrieval_check --mode dense hybrid

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
import os
import re
from dataclasses import dataclass

from identity_mcp import bank, retrieval
from identity_mcp.validation import SCHEMA

from . import cases as cases_mod

DEPTH = 10  # how far down the ranking to look for the twin


@dataclass(frozen=True)
class Score:
    """Retrieval quality for one strategy over a set of paraphrase cases."""

    mode: str
    cases: int
    recall: float
    mrr: float
    k: int


async def evaluate(mode: str, k: int = 4) -> Score:
    """Score retrieval `mode` (hybrid, dense or lexical) on every paraphrase case.

    Uses ``min_score=0`` so the embedding threshold does not hide the comparison; only the ranking is judged.
    """
    previous = os.environ.get("IDENTITY_MCP_RETRIEVAL")
    os.environ["IDENTITY_MCP_RETRIEVAL"] = mode
    try:
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
        return Score(mode, n, found / n, reciprocal / n, k)
    finally:
        if previous is None:
            os.environ.pop("IDENTITY_MCP_RETRIEVAL", None)
        else:
            os.environ["IDENTITY_MCP_RETRIEVAL"] = previous


@dataclass(frozen=True)
class HeldOut:
    """Neighbour quality for one strategy when each paraphrase's own entry is hidden.

    Attributes:
        mode: The retrieval strategy.
        cases: Number of paraphrase cases.
        same_tables: Share of cases where at least one of the top k examples uses exactly the twin's tables.
        table_overlap: Mean Jaccard similarity between the examples' tables and the twin's.
        tag_overlap: Mean Jaccard similarity between the examples' tags and the twin's.
        k: How many examples were retrieved per case.
    """

    mode: str
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


async def evaluate_held_out(mode: str, k: int = 4) -> HeldOut:
    """Score the neighbours retrieval returns for each paraphrase case with its source entry hidden."""
    by_id = {e.id: e for e in bank.load()}
    previous = os.environ.get("IDENTITY_MCP_RETRIEVAL")
    os.environ["IDENTITY_MCP_RETRIEVAL"] = mode
    try:
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
        return HeldOut(mode, n, same / n, table_sum / n, tag_sum / n, k)
    finally:
        if previous is None:
            os.environ.pop("IDENTITY_MCP_RETRIEVAL", None)
        else:
            os.environ["IDENTITY_MCP_RETRIEVAL"] = previous


async def main(modes: list[str], k: int) -> None:
    """Run both checks for each mode and print two small comparison tables."""
    print("source entry not hidden:")
    print(f"{'mode':<10}{'cases':>7}{f'recall@{k}':>11}{'MRR':>8}")
    for mode in modes:
        s = await evaluate(mode, k)
        print(f"{s.mode:<10}{s.cases:>7}{s.recall:>11.2f}{s.mrr:>8.2f}")
    print("\nsource entry hidden (do the neighbours look like the right answer?):")
    print(f"{'mode':<10}{'cases':>7}{'same tables':>13}{'table overlap':>15}{'tag overlap':>13}")
    for mode in modes:
        h = await evaluate_held_out(mode, k)
        print(f"{h.mode:<10}{h.cases:>7}{h.same_tables:>13.2f}{h.table_overlap:>15.2f}{h.tag_overlap:>13.2f}")


def cli() -> None:
    """Command-line entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", nargs="+", choices=retrieval.MODES, default=list(retrieval.MODES))
    p.add_argument("--k", type=int, default=4, help="size of the top-k")
    args = p.parse_args()
    asyncio.run(main(args.mode, args.k))


if __name__ == "__main__":
    cli()
