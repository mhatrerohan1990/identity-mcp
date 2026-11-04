"""Where feedback is kept: a small SQLite file of its own.

It is separate from the identity data on purpose. The data database is opened read-only everywhere; this file is the one
place the application writes. Location: ``$IDENTITY_MCP_FEEDBACK_DB``, default ``feedback.db`` in the working directory
(ignored by git).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    rating    INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    question  TEXT NOT NULL,
    answer    TEXT NOT NULL,
    comment   TEXT NOT NULL,
    mode      TEXT NOT NULL,
    verified  INTEGER,
    steps     TEXT NOT NULL,
    status    TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'promoted', 'dismissed'))
)
"""

UP, DOWN = 1, -1


@dataclass(frozen=True)
class Step:
    """One query behind an answer: what a tool was asked and the SQL it ran.

    Attributes:
        tool: ``query_identity`` or ``query_activity`` (agent), or ``sql`` for the single-query pipeline.
        question: The plain-English question the SQL answers; for a follow-up this is the rewritten, standalone form.
        sql: The query that ran (may be empty if none was produced).
        linked: True when the query read the result of an earlier step through ``filter_from``; such a query only makes
            sense together with that step, so it cannot become a stand-alone bank example.
        error: The error the step ended with, or empty.
    """
    tool: str
    question: str
    sql: str = ""
    linked: bool = False
    error: str = ""


@dataclass(frozen=True)
class Feedback:
    """One piece of feedback.

    Attributes:
        id: Row id.
        ts: When it was given (UTC, ISO 8601).
        rating: ``UP`` (1) or ``DOWN`` (-1).
        question: What the user asked.
        answer: The answer they rated.
        comment: Optional free text, mostly for thumbs down ("what was wrong").
        mode: ``agent`` or ``single``.
        verified: Whether the answer passed ``agent.verify`` (None when not checked).
        steps: The queries behind the answer.
        status: ``new`` until a reviewer has dealt with it, then ``promoted`` or ``dismissed``.
    """
    id: int
    ts: str
    rating: int
    question: str
    answer: str
    comment: str
    mode: str
    verified: bool | None
    steps: list[Step] = field(default_factory=list)
    status: str = "new"


def feedback_path() -> Path:
    """The feedback file: ``$IDENTITY_MCP_FEEDBACK_DB`` or ``feedback.db``."""
    return Path(os.environ.get("IDENTITY_MCP_FEEDBACK_DB", "feedback.db"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the feedback database. Rows come back as ``sqlite3.Row``."""
    con = sqlite3.connect(path or feedback_path())
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    return con


def add(con: sqlite3.Connection, *, rating: int, question: str, answer: str, comment: str = "", mode: str = "agent",
        verified: bool | None = None, steps: list[Step] | None = None) -> int:
    """Store one piece of feedback and return its id."""
    cur = con.execute(
        "INSERT INTO feedback (ts, rating, question, answer, comment, mode, verified, steps) VALUES (?,?,?,?,?,?,?,?)",
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), rating, question, answer, comment, mode,
         None if verified is None else int(verified), json.dumps([asdict(s) for s in steps or []])),
    )
    con.commit()
    return cur.lastrowid


def _from_row(row: sqlite3.Row) -> Feedback:
    """A database row as a :class:`Feedback`."""
    return Feedback(row["id"], row["ts"], row["rating"], row["question"], row["answer"], row["comment"], row["mode"],
                    None if row["verified"] is None else bool(row["verified"]),
                    [Step(**s) for s in json.loads(row["steps"])], row["status"])


def pending(con: sqlite3.Connection, rating: int | None = None) -> list[Feedback]:
    """Feedback nobody has reviewed yet, oldest first; only thumbs up or only thumbs down when `rating` is given."""
    sql, args = "SELECT * FROM feedback WHERE status = 'new'", []
    if rating is not None:
        sql, args = sql + " AND rating = ?", [rating]
    return [_from_row(r) for r in con.execute(sql + " ORDER BY id", args)]


def mark(con: sqlite3.Connection, feedback_id: int, status: str) -> None:
    """Set a feedback item's status (``promoted`` or ``dismissed``) so it is not shown for review again."""
    con.execute("UPDATE feedback SET status = ? WHERE id = ?", (status, feedback_id))
    con.commit()


def counts(con: sqlite3.Connection) -> dict[str, int]:
    """How many items are in each state, e.g. ``{"up": 3, "down": 1, "new": 2, "total": 4}``."""
    row = con.execute("SELECT COUNT(*) t, SUM(rating = 1) u, SUM(rating = -1) d, SUM(status = 'new') n FROM feedback").fetchone()
    return {"total": row["t"], "up": row["u"] or 0, "down": row["d"] or 0, "new": row["n"] or 0}
