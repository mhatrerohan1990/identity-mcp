"""Shared SQLite access. Connections are always read-only."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


SCHEMA_SQL = (Path(__file__).parent / "data" / "schema.sql").read_text()


def db_path() -> Path:
    return Path(os.environ.get("IDENTITY_MCP_DB", "app.db"))


def connect_ro(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run: uv run python -m identity_mcp.data.seed")
    # check_same_thread=False: callers may hand the connection to a worker thread.
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con
