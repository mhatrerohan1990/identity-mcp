"""Run the eval suite against the local model.

    uv run python -m evals.run                      # k=0 and k=4, repair loop on
    uv run python -m evals.run --k 4 --repairs 0 2  # does the repair loop help?
    uv run python -m evals.run --kind paraphrase --limit 10 --out /tmp/quick.json

Needs Ollama running and a seeded app.db (run from the repo root). Each case runs the real pipeline up to and
including validation/repair and execution; the plain-English answer step is skipped because it does not affect
the SQL and is the slowest call. Results are graded against the gold query's rows (see metrics.py).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from identity_mcp import llm, pipeline, retrieval
from identity_mcp.db import db_path
from identity_mcp.logs import configure

from . import cases as cases_mod
from . import metrics

PASS = {"exact", "complete", "entities"}


@dataclass(frozen=True)
class Config:
    k: int
    repairs: int

    @property
    def name(self) -> str:
        return f"k={self.k} repairs={self.repairs}"


@dataclass
class Result:
    case: str
    kind: str
    config: str
    question: str
    outcome: str  # exact | complete | entities | wrong | rejected | exec_error | llm_error | harmless (safety)
    sql: str | None = None
    first_try_valid: bool | None = None
    repairs: int = 0
    first_errors: list[str] = field(default_factory=list)
    final_errors: list[str] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    detail: str = ""
    ms: int = 0


def _fingerprint() -> str:
    return hashlib.sha256(Path(db_path()).read_bytes()).hexdigest()


def _rows(sql: str) -> list[list[Any]]:
    return pipeline.execute(sql)["rows"]


async def run_case(case: cases_mod.Case, cfg: Config) -> Result:
    started = time.monotonic()
    res = Result(case.id, case.kind, cfg.name, case.question, "llm_error")
    before = _fingerprint() if case.kind == "safety" else None
    try:
        hits = await retrieval.retrieve(case.question, k=cfg.k, exclude=case.hold_out) if cfg.k else []
        res.retrieved = [h.example.id for h in hits]
        draft = await pipeline.produce_sql(case.question, hits)
    except (llm.LLMError, FileNotFoundError) as e:
        res.detail = str(e)
        res.ms = round((time.monotonic() - started) * 1000)
        return res
    res.sql, res.repairs = draft.sql, draft.repairs
    res.first_try_valid = draft.attempts[0]["ok"]
    res.first_errors = [] if res.first_try_valid else draft.attempts[0]["errors"]
    if not draft.check.ok:
        res.outcome, res.final_errors = "rejected", draft.check.errors
    else:
        try:
            actual = await asyncio.to_thread(_rows, draft.sql)
        except Exception as e:  # a failed run is a result, not a crash
            res.outcome, res.detail = "exec_error", f"{type(e).__name__}: {e}"
        else:
            if case.gold_sql is None:
                res.outcome = "harmless"
            else:
                res.outcome = metrics.grade(_rows(case.gold_sql), actual)
    if before is not None and _fingerprint() != before:
        res.outcome, res.detail = "database_changed", "the database file changed during a safety case"
    res.ms = round((time.monotonic() - started) * 1000)
    return res


def summarize(results: list[Result]) -> dict[str, Any]:
    scored = [r for r in results if r.kind != "safety"]
    safety = [r for r in results if r.kind == "safety"]
    n = len(scored)
    count = Counter(r.outcome for r in scored)
    first_failed = [r for r in results if r.first_try_valid is False]
    return {
        "scored_cases": n,
        "pass": sum(count[o] for o in PASS),
        "exact": count["exact"],
        "complete_only": count["complete"],
        "entities_only": count["entities"],
        "wrong": count["wrong"],
        "rejected": count["rejected"],
        "exec_error": count["exec_error"],
        "llm_error": count["llm_error"],
        "valid_first_try": sum(1 for r in results if r.first_try_valid),
        "invalid_first_try": len(first_failed),
        "repaired_to_valid": sum(1 for r in first_failed if r.outcome != "rejected"),
        "repair_calls": sum(r.repairs for r in results),
        "safety_cases": len(safety),
        "safety_rejected": sum(1 for r in safety if r.outcome == "rejected"),
        "safety_ran_harmlessly": sum(1 for r in safety if r.outcome == "harmless"),
        "safety_violations": sum(1 for r in safety if r.outcome in ("database_changed", "exec_error")),
        "median_ms": round(statistics.median(r.ms for r in results)) if results else 0,
    }


def by_kind(results: list[Result]) -> dict[str, str]:
    out = {}
    for kind in ("paraphrase", "novel"):
        rs = [r for r in results if r.kind == kind]
        if rs:
            out[kind] = f"{sum(r.outcome in PASS for r in rs)}/{len(rs)}"
    return out


def render(summaries: dict[str, dict], kinds: dict[str, dict]) -> str:
    names = list(summaries)
    rows = [
        ("pass (right rows)", lambda s, n: f"{s['pass']}/{s['scored_cases']}"),
        ("  exact", lambda s, n: s["exact"]),
        ("  all gold columns, extras", lambda s, n: s["complete_only"]),
        ("  right rows, fewer columns", lambda s, n: s["entities_only"]),
        ("wrong result", lambda s, n: s["wrong"]),
        ("no valid query (rejected)", lambda s, n: s["rejected"]),
        ("failed to run / model error", lambda s, n: s["exec_error"] + s["llm_error"]),
        ("  paraphrase pass", lambda s, n: kinds[n].get("paraphrase", "-")),
        ("  novel pass", lambda s, n: kinds[n].get("novel", "-")),
        ("valid on first try", lambda s, n: f"{s['valid_first_try']}/{s['valid_first_try'] + s['invalid_first_try']}"),
        ("repaired to valid", lambda s, n: f"{s['repaired_to_valid']}/{s['invalid_first_try']}"),
        ("repair calls", lambda s, n: s["repair_calls"]),
        ("safety: rejected / ran harmlessly", lambda s, n: f"{s['safety_rejected']} / {s['safety_ran_harmlessly']} of {s['safety_cases']}"),
        ("safety violations", lambda s, n: s["safety_violations"]),
        ("median ms per case", lambda s, n: s["median_ms"]),
    ]
    width = max(len(label) for label, _ in rows) + 2
    lines = [" " * width + "".join(f"{n:>20}" for n in names)]
    lines += [f"{label:<{width}}" + "".join(f"{fn(summaries[n], n)!s:>20}" for n in names) for label, fn in rows]
    return "\n".join(lines)


def diffs(results: list[Result], names: list[str]) -> list[str]:
    """Cases whose outcome differs between configs (the interesting ones)."""
    by_case: dict[str, dict[str, Result]] = {}
    for r in results:
        by_case.setdefault(r.case, {})[r.config] = r
    lines = []
    for cid, per in by_case.items():
        outs = [per[n].outcome for n in names if n in per]
        if len(set(outs)) > 1:
            lines.append(f"{cid:<7} " + "  ".join(f"{n}: {per[n].outcome}" for n in names if n in per) + f"  | {next(iter(per.values())).question[:60]}")
    return lines


async def main(args: argparse.Namespace) -> None:
    if args.verbose:
        configure()
    else:  # per-case progress lines are the output; the pipeline's own logs would bury them
        logging.getLogger("identity_mcp").setLevel(logging.CRITICAL)
    all_cases = cases_mod.load()
    selected = [c for c in all_cases if (not args.kind or c.kind in args.kind) and (not args.case or c.id in args.case)]
    if args.limit:
        selected = selected[: args.limit]
    configs = [Config(k, r) for r in args.repairs for k in args.k]
    print(f"{len(selected)} cases x {len(configs)} configs; model={llm.MODEL}", flush=True)
    results: list[Result] = []
    try:
        for cfg in configs:
            os.environ["IDENTITY_MCP_MAX_REPAIRS"] = str(cfg.repairs)
            for i, case in enumerate(selected, 1):
                r = await run_case(case, cfg)
                results.append(r)
                print(f"[{cfg.name}] {i:>3}/{len(selected)} {case.id:<7} {r.outcome:<14} {r.ms:>6} ms  {case.question[:55]}", flush=True)
    except KeyboardInterrupt:
        print("interrupted; reporting what finished", flush=True)
    names = [c.name for c in configs]
    summaries = {n: summarize([r for r in results if r.config == n]) for n in names if any(r.config == n for r in results)}
    kinds = {n: by_kind([r for r in results if r.config == n]) for n in summaries}
    print("\n" + render(summaries, kinds))
    d = diffs(results, list(summaries))
    if d:
        print("\ncases that differ between configs:\n" + "\n".join(d))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        meta = {"date": datetime.now().isoformat(timespec="seconds"), "model": llm.MODEL, "embed_model": llm.EMBED_MODEL,
                "cases": len(selected), "configs": names}
        out.write_text(json.dumps({"meta": meta, "summary": summaries, "kinds": kinds, "results": [r.__dict__ for r in results]}, indent=1))
        print(f"\nwrote {out}")


def cli() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--k", type=int, nargs="+", default=[0, 4], help="examples retrieved per question (0 = none)")
    p.add_argument("--repairs", type=int, nargs="+", default=[2], help="max repair attempts")
    p.add_argument("--kind", nargs="+", choices=["paraphrase", "novel", "safety"])
    p.add_argument("--case", nargs="+", help="case ids, e.g. p-058 s-01")
    p.add_argument("--limit", type=int, help="first N selected cases")
    p.add_argument("--verbose", action="store_true", help="show the pipeline's flow/prompt logs")
    p.add_argument("--out", default="evals/results/latest.json", help="JSON report path ('' to skip)")
    asyncio.run(main(p.parse_args()))


if __name__ == "__main__":
    cli()
