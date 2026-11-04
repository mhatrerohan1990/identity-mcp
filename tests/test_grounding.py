"""Value grounding: filters on values the data does not contain are caught, with the real values suggested."""

import asyncio
import json
import os
import sqlite3

import pytest

from identity_mcp.rag import bank
from identity_mcp.guardrails import grounding
from identity_mcp.core import llm
from identity_mcp import pipeline
from identity_mcp.data import seed

run = asyncio.run


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cat = grounding.build_catalog(con)
    con.close()
    return cat


def problems(sql, catalog):
    return grounding.check(sql, catalog)


def test_catalog_holds_categories_but_not_free_text_or_dates(catalog):
    assert catalog[("users", "status")] == {"active", "suspended", "terminated"}
    assert "Engineering" in catalog[("users", "department")]
    assert "Snowflake" in catalog[("applications", "name")]
    for free_text in [("users", "email"), ("users", "name"), ("users", "created_at"), ("activity_events", "ts"),
                      ("activity_events", "ip"), ("users", "terminated_at")]:
        assert free_text not in catalog


def test_all_bank_queries_are_grounded(catalog):
    flagged = [(e.id, p) for e in bank.load() if (p := problems(e.sql, catalog))]
    assert flagged == []


@pytest.mark.parametrize("sql", [
    "SELECT name FROM users WHERE department = 'Engineering'",
    "SELECT name FROM users WHERE department IN ('Sales', 'Legal')",
    "SELECT name FROM users WHERE status <> 'active'",
    "SELECT name FROM users WHERE name LIKE '%Arjun%'",      # fuzzy match: not an equality
    "SELECT name FROM users WHERE name = 'Nobody Atall'",    # names are free text, not a category
    "SELECT name FROM users WHERE department = 'Eng' || 'ineering'",  # not a plain literal
    "SELECT name FROM users WHERE department = (SELECT department FROM users WHERE id = 1)",
    "SELECT COUNT(*) FROM users",
])
def test_good_or_unchecked_queries_pass(sql, catalog):
    assert problems(sql, catalog) == []


def test_unknown_value_is_reported_with_the_closest_real_one(catalog):
    (msg,) = problems("SELECT name FROM users WHERE department = 'Eng'", catalog)
    assert "users.department has no value 'Eng'" in msg and "'Engineering'" in msg
    assert "All values:" in msg and "'Sales'" in msg  # only 7 departments: list them all


def test_wrong_case_is_called_out(catalog):
    (msg,) = problems("SELECT name FROM users WHERE status = 'ACTIVE'", catalog)
    assert "Closest real values: 'active'" in msg and "case-sensitive" in msg


def test_big_columns_get_suggestions_not_the_whole_list(catalog):
    (msg,) = problems("SELECT name FROM users WHERE title = 'Security Analist'", catalog)
    assert "'Security Analyst'" in msg and "All values:" not in msg  # 23 titles: too many to list


def test_each_bad_value_in_an_in_list_is_reported_once(catalog):
    msgs = problems("SELECT 1 FROM users WHERE department IN ('Sales', 'Mars', 'Venus', 'Mars')", catalog)
    assert len(msgs) == 2 and "'Mars'" in msgs[0] and "'Venus'" in msgs[1]


def test_literal_on_the_left_and_aliases_are_resolved(catalog):
    assert problems("SELECT 1 FROM users u WHERE 'Mars' = u.department", catalog)
    sql = """SELECT u.name FROM users u JOIN access_grants g ON g.user_id = u.id
             JOIN entitlements e ON e.id = g.entitlement_id JOIN applications a ON a.id = e.app_id
             WHERE a.name = 'Snowflak' AND u.status = 'active'"""
    (msg,) = problems(sql, catalog)
    assert "applications.name has no value 'Snowflak'" in msg and "'Snowflake'" in msg


def test_same_column_name_on_different_tables_is_told_apart(catalog):
    # 'Admin' is a real entitlement name but not a real group name
    assert problems("SELECT 1 FROM entitlements e WHERE e.name = 'Admin'", catalog) == []
    (msg,) = problems("SELECT 1 FROM groups g WHERE g.name = 'Admin'", catalog)
    assert msg.startswith("groups.name has no value 'Admin'")


def test_subqueries_and_ctes_are_checked(catalog):
    assert problems("SELECT name FROM users WHERE id IN (SELECT user_id FROM group_members) AND status = 'gone'", catalog)
    assert problems("WITH t AS (SELECT * FROM users WHERE department = 'Mars') SELECT name FROM t", catalog)


def test_malformed_sql_is_left_to_validation(catalog):
    assert problems("SELEC nonsense", catalog) == []


def test_no_catalog_means_no_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "missing.db"))
    assert grounding.catalog() is None
    assert grounding.check("SELECT 1 FROM users WHERE department = 'Mars'") == []  # fails open


def test_catalog_is_rebuilt_when_the_database_changes(monkeypatch, tmp_path):
    path = tmp_path / "app.db"
    seed.build(path)
    monkeypatch.setenv("IDENTITY_MCP_DB", str(path))
    monkeypatch.setattr(grounding, "_cache", None)
    assert ("users", "status") in grounding.catalog()
    assert grounding.catalog() is grounding.catalog()  # cached
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("UPDATE users SET department = 'Platform' WHERE id = 1")
    con.commit()
    con.close()
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert "Platform" in grounding.catalog()[("users", "department")]


# --- in the pipeline -------------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))
    monkeypatch.setattr(grounding, "_cache", None)


def scripted(sqls, calls):
    async def chat(messages, *, schema=None, timeout=120):
        if not schema:
            return "summary"
        calls.append(messages)
        return json.dumps({"sql": sqls[min(len(calls) - 1, len(sqls) - 1)]})
    return chat


BAD = "SELECT name FROM users WHERE department = 'Eng' AND status = 'active'"
GOOD = "SELECT name FROM users WHERE department = 'Engineering' AND status = 'active'"


def test_wrong_value_is_repaired_through_the_loop(env, monkeypatch):
    calls, executed = [], []
    real = pipeline.execute
    monkeypatch.setattr(pipeline, "execute", lambda sql, *a: (executed.append(sql), real(sql, *a))[1])
    monkeypatch.setattr(llm, "chat", scripted([BAD, GOOD], calls))
    out = run(pipeline.ask("active engineers", k=0))
    assert out["sql"] == GOOD and out["repairs"] == 1 and executed == [GOOD] and out["rows"]
    first = out["attempts"][0]
    assert first["ok"] is False and first["stage"] == "grounding" and "'Engineering'" in first["errors"][0]
    assert "users.department has no value 'Eng'" in calls[1][-1]["content"]  # the model was told, in the repair turn


def test_value_that_does_not_exist_is_reported_not_run_or_swapped(env, monkeypatch):
    calls, executed = [], []
    monkeypatch.setattr(pipeline, "execute", lambda sql, *a: executed.append(sql))
    sql = "SELECT name FROM users WHERE department = 'Mars'"
    monkeypatch.setattr(llm, "chat", scripted([sql], calls))  # the model insists
    out = run(pipeline.ask("who works in Mars?", k=0))
    assert executed == [] and out["sql"] == sql and "grounding" in out["error"]
    assert out["answer"].startswith("I couldn't find some of what you asked about in the data")
    assert "users.department has no value 'Mars'" in out["answer"] and "'Engineering'" in out["answer"]
    assert len(calls) == 2  # it repeated the same query, so we stopped instead of retrying


def test_validation_runs_before_grounding(env):
    result = pipeline.check_sql("SELECT nope FROM users WHERE department = 'Mars'")
    assert result.stage == "static"  # a broken query is reported as broken first
