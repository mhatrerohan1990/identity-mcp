"""Review feedback: ``uv run python -m identity_mcp.feedback``.

Walks through the items nobody has reviewed yet, one at a time, and asks what to do:

* **thumbs up** - each step that qualifies is shown with its SQL and row count; ``a`` adds it to the question bank
* **thumbs down** - the question, answer, comment and the SQL are shown; ``g`` lets you type the correct SQL, which is
  validated, run, and added to the eval set as a new case

``s`` skips an item (it stays pending), ``d`` dismisses it for good, ``q`` quits. ``--stats`` only prints counts.
Everything written is checked first (see ``promote.py``); nothing is added without a keypress from you.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from . import promote, store


def review_up(item: store.Feedback, ask: Callable[[str], str], out: Callable[[str], None]) -> str:
    """Offer each qualifying step of a thumbs-up item to the bank. Returns the item's new status, ``skip`` or ``quit``."""
    added = 0
    for step in item.steps:
        check = promote.check_step(step)
        out(f"\n  step ({step.tool}): {step.question}\n  {step.sql or '(no SQL)'}")
        if not check.ok:
            out(f"  cannot add: {'; '.join(check.reasons)}")
            continue
        choice = ask(f"  {check.row_count} row(s). [a]dd to the bank, [n]ext, [s]kip item, [d]ismiss item, [q]uit > ").strip().lower()
        if choice == "a":
            out(f"  added as {promote.add_to_bank(step)}")
            added += 1
        elif choice in ("s", "d", "q"):
            return {"s": "skip", "d": "dismissed", "q": "quit"}[choice]
    return "promoted" if added else "dismissed"


def read_sql(ask: Callable[[str], str]) -> str:
    """Read SQL typed over several lines, ended by an empty line."""
    lines = []
    while (line := ask("  sql> " if not lines else "      > ")).strip():
        lines.append(line)
    return "\n".join(lines)


def review_down(item: store.Feedback, ask: Callable[[str], str], out: Callable[[str], None]) -> str:
    """Offer to turn a thumbs-down item into an eval case once the reviewer supplies the right SQL."""
    for step in item.steps:
        out(f"\n  step ({step.tool}): {step.question}\n  {step.sql or '(no SQL)'}" + (f"\n  error: {step.error}" if step.error else ""))
    while True:
        choice = ask("  [g]ive the correct SQL, [s]kip item, [d]ismiss item, [q]uit > ").strip().lower()
        if choice == "g":
            sql = read_sql(ask)
            question = (ask(f"  question for the eval case (Enter keeps: {item.question!r}) > ").strip()) or item.question
            tool = item.steps[0].tool if item.steps else "sql"
            try:
                out(f"  added as eval case {promote.add_eval_case(question, sql, promote.scope_for(tool))}")
                return "promoted"
            except ValueError as e:
                out(f"  not added: {e}")
        elif choice in ("s", "d", "q"):
            return {"s": "skip", "d": "dismissed", "q": "quit"}[choice]


def review(con, ask: Callable[[str], str] = input, out: Callable[[str], None] = print, rating: int | None = None) -> dict[str, int]:
    """Run the review over the pending items of `con`; returns how many ended in each state (``promoted``, ``dismissed``, ``skip``)."""
    tally = {"promoted": 0, "dismissed": 0, "skip": 0}
    for item in store.pending(con, rating):
        kind = "thumbs up" if item.rating == store.UP else "thumbs down"
        out(f"\n#{item.id} {kind} ({item.mode}, {item.ts})\n  Q: {item.question}\n  A: {item.answer}")
        if item.comment:
            out(f"  comment: {item.comment}")
        outcome = (review_up if item.rating == store.UP else review_down)(item, ask, out)
        if outcome == "quit":
            break
        if outcome in ("promoted", "dismissed"):
            store.mark(con, item.id, outcome)
        tally[outcome] += 1
    return tally


def cli() -> None:
    """Parse the command line and run the review."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stats", action="store_true", help="only print how much feedback there is")
    parser.add_argument("--only", choices=["up", "down"], help="review only thumbs up or only thumbs down")
    args = parser.parse_args()
    con = store.connect()
    if args.stats:
        print(store.counts(con))
        return
    rating = {"up": store.UP, "down": store.DOWN}.get(args.only)
    print(f"{len(store.pending(con, rating))} item(s) to review in {store.feedback_path()}")
    print(review(con, rating=rating))


if __name__ == "__main__":
    cli()
