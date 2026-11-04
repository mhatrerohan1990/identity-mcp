"""SQLite access for the API: one read-only connection per request."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import HTTPException

from ..db import connect_ro


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: a read-only connection for the length of one request.

    Closed afterwards. Responds 503 with the seed command when the database file does not exist.
    """
    try:
        con = connect_ro()
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    try:
        yield con
    finally:
        con.close()
