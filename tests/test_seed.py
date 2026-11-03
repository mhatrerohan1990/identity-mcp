import sqlite3
from datetime import date, timedelta

import pytest

from grantlens.data import seed


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    con = sqlite3.connect(path)
    yield con
    con.close()


def test_deterministic(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    seed.build(a)
    seed.build(b)
    q = "SELECT * FROM access_grants ORDER BY id"
    assert sqlite3.connect(a).execute(q).fetchall() == sqlite3.connect(b).execute(q).fetchall()


def test_orphaned_access(con):
    n = con.execute(
        "SELECT COUNT(DISTINCT u.id) FROM users u JOIN access_grants g ON g.user_id = u.id WHERE u.status = 'terminated'"
    ).fetchone()[0]
    assert n == seed.PLANTED["orphaned_users"]


def test_dormant_admins(con):
    cutoff = (seed.TODAY - timedelta(days=90)).isoformat()
    n = con.execute(
        """SELECT COUNT(*) FROM users u WHERE u.status = 'active'
           AND EXISTS (SELECT 1 FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id
                       WHERE g.user_id = u.id AND e.is_admin = 1)
           AND COALESCE((SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id), '') < ?""",
        (cutoff,),
    ).fetchone()[0]
    assert n == seed.PLANTED["dormant_admins"]


def test_privileged_without_mfa(con):
    n = con.execute(
        """SELECT COUNT(DISTINCT u.id) FROM users u
           JOIN group_members m ON m.user_id = u.id JOIN groups g ON g.id = m.group_id
           WHERE g.is_privileged = 1 AND u.mfa_enabled = 0 AND u.status = 'active'"""
    ).fetchone()[0]
    assert n == seed.PLANTED["privileged_no_mfa"]


def test_foreign_keys_clean(con):
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
