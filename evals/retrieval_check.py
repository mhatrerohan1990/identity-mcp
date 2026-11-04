"""Does retrieval put the right bank entry near the top for a reworded question?

    uv run python -m evals.retrieval_check                       # all three strategies
    uv run python -m evals.retrieval_check --mode hybrid dense

Each paraphrase case in ``cases.json`` was written from one bank entry (its "twin"). Here the twin is *not* hidden:
the question is whether it comes back, and how high. This needs only the embedding model, not the chat model, so it
is far cheaper than the full eval, and it isolates retrieval from SQL generation.

Reported per strategy: **recall@k** (share of cases whose twin is in the top k) and **MRR** (mean of 1/rank of the
twin; 1.0 means always first, 0 means never found within the top 10).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from identity_mcp import retrieval

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
    import os

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


async def main(modes: list[str], k: int) -> None:
    """Evaluate each mode and print a small comparison table."""
    print(f"{'mode':<10}{'cases':>7}{f'recall@{k}':>11}{'MRR':>8}")
    for mode in modes:
        s = await evaluate(mode, k)
        print(f"{s.mode:<10}{s.cases:>7}{s.recall:>11.2f}{s.mrr:>8.2f}")


def cli() -> None:
    """Command-line entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", nargs="+", choices=retrieval.MODES, default=list(retrieval.MODES))
    p.add_argument("--k", type=int, default=4, help="size of the top-k used for recall")
    args = p.parse_args()
    asyncio.run(main(args.mode, args.k))


if __name__ == "__main__":
    cli()
