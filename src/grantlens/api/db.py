"""SQLite access for the API: one read-only connection per request."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException


def db_path() -> Path:
    return Path(os.environ.get("GRANTLENS_DB", "app.db"))


def get_db() -> Iterator[sqlite3.Connection]:
    path = db_path()
    if not path.exists():
        raise HTTPException(503, f"{path} not found. Run: uv run python -m grantlens.data.seed")
    # check_same_thread=False: FastAPI may resolve the dependency and run the handler on different threads.
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()
