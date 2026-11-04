"""Validates examples/examples.md: every expected query must run and (where it matters) give the right answer."""

import re
import sqlite3

import pytest
import sqlglot
from sqlglot import exp

from identity_mcp.rag import bank
from identity_mcp.data import seed

BANK = bank.bank_path()


def load():
    return [{"id": e.id, "q": e.question, "tags": set(e.tags), "sql": e.sql, "notes": e.notes} for e in bank.load()]


ENTRIES = load()
BY_ID = {e["id"]: e for e in ENTRIES}


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    yield con
    con.close()


def test_bank_has_104_well_formed_entries():
    assert BANK.read_text().count("\n## ex-") == len(ENTRIES) == 104  # the regex dropped nothing
    assert [e["id"] for e in ENTRIES] == [f"ex-{i:03d}" for i in range(1, 105)]
    assert len({e["q"].lower() for e in ENTRIES}) == 104, "duplicate questions"
    assert len({re.sub(r"\s+", " ", e["sql"]) for e in ENTRIES}) == 104, "duplicate queries"


@pytest.mark.parametrize("e", ENTRIES, ids=lambda e: e["id"])
def test_query_is_a_single_select_and_runs(e, con):
    stmts = sqlglot.parse(e["sql"], read="sqlite")
    assert len(stmts) == 1 and isinstance(stmts[0], (exp.Select, exp.Union)), "must be exactly one SELECT"
    assert "date('now')" not in e["sql"].lower() and "datetime('now')" not in e["sql"].lower()
    rows = con.execute(e["sql"]).fetchall()
    if "empty-ok" in e["tags"]:
        assert rows == [], "tagged empty-ok but returns rows; fix the tag or the query"
    else:
        assert rows, "returns no rows; if that is the correct answer, tag it empty-ok"


def test_empty_ok_entries_exist():
    assert any("empty-ok" in e["tags"] for e in ENTRIES)


def ask(con, question):
    (e,) = [e for e in ENTRIES if e["q"] == question]
    return con.execute(e["sql"]).fetchall()


def test_planted_findings_match_the_seed(con):
    # the questions that exist to find the planted problems must find exactly them
    assert ask(con, "How many terminated users still have access to anything?") == [(seed.PLANTED["orphaned_users"],)]
    assert len(ask(con, "Which terminated users still have access?")) == seed.PLANTED["orphaned_users"]
    assert ask(con, "How many dormant admins do we have?") == [(seed.PLANTED["dormant_admins"],)]
    assert len(ask(con, "Which active admins have had no activity in the last 90 days?")) == seed.PLANTED["dormant_admins"]
    assert ask(con, "How many privileged users lack MFA?") == [(seed.PLANTED["privileged_no_mfa"],)]
    assert len(ask(con, "Which active members of privileged groups don't have MFA enabled?")) == seed.PLANTED["privileged_no_mfa"]


def test_basic_counts(con):
    assert ask(con, "How many users are there?") == [(seed.N_USERS,)]
    assert ask(con, "How many users are terminated?") == [(50,)]
    assert len(ask(con, "Can you show me all departments in my tenant?")) == 7
