import asyncio
import logging
import sqlite3

import pytest

from identity_mcp import bank, llm, pipeline, validation
from identity_mcp.data import seed

run = asyncio.run


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    yield con
    con.close()


def test_every_bank_query_passes_validation(con):
    """No false alarms: the 100 known-good queries must all validate."""
    rejected = [(e.id, v.errors) for e in bank.load() if not (v := validation.validate(e.sql, con)).ok]
    assert rejected == []


@pytest.mark.parametrize("sql, stage, message", [
    ("SELECT i.integration FROM entitlements i", "static", "Unknown column: integration"),
    ("SELECT g.name FROM access_grants g", "static", "Unknown column: name"),
    ("SELECT 1 FROM users g JOIN groups g ON g.id = g.id", "static", "Alias already used: g"),
    ("SELECT * FROM employees", "static", "Unknown table 'employees'"),
    ("SELECT name FROM sqlite_master", "static", "Unknown table 'sqlite_master'"),
    ("SELECT * FROM pragma_table_info('users')", "static", "Table-valued functions"),
    ("DELETE FROM users", "static", "got DELETE"),
    ("PRAGMA table_info(users)", "static", "got PRAGMA"),
    ("ATTACH DATABASE '/tmp/x.db' AS x", "static", "got ATTACH"),
    ("SELECT 1; DROP TABLE users", "static", "found 2"),
    ("SELECT load_extension('evil')", "static", "load_extension() is not allowed"),
    ("SELECT COUNT(*) FROM users WHERE created_at > date('now', '-1 year')", "static", "Do not use 'now'"),
    ("SELECT CURRENT_DATE", "static", "CURRENT_DATE"),
    ("SELEC name FROM users", "static", "syntax error"),
    ("SELECT COUNT(* FROM users", "static", "syntax error"),
    ("   ", "static", "empty"),
    ("-- nothing", "static", "empty"),
    ("SELECT frobnicate(name) FROM users", "dry_run", "no such function: frobnicate"),
])
def test_rejects_bad_queries_at_the_right_stage(con, sql, stage, message):
    v = validation.validate(sql, con)
    assert not v.ok and v.stage == stage
    assert any(message in e for e in v.errors), v.errors


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT COUNT(*) FROM main.users",
    "WITH t AS (SELECT id FROM users) SELECT COUNT(*) FROM t",
    "SELECT id FROM users UNION SELECT id FROM groups",
    "SELECT u.name, (SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id) AS last FROM users u ORDER BY last",
])
def test_accepts_legitimate_queries(con, sql):
    assert validation.validate(sql, con).ok


def test_column_errors_are_clean_and_say_what_exists():
    (msg,) = validation.validate_static("SELECT nope FROM users")
    assert "Line:" not in msg and "Col:" not in msg  # parser position noise removed
    assert "'nope' could not be resolved" in msg
    assert "users(id, email, name, department" in msg  # what the model (or user) can use instead
    (msg,) = validation.validate_static("SELECT i.integration FROM entitlements i")
    assert "entitlements(id, app_id, name, is_admin)" in msg and "users(" not in msg  # only tables in play


def test_static_stage_never_touches_the_database():
    # no connection exists in this test; a static-only rejection must not need one
    assert validation.validate_static("DELETE FROM users")
    assert validation.validate_static("SELECT COUNT(*) FROM users") == []


def test_dry_run_does_not_execute(con):
    # EXPLAIN compiles but does not run: a query that would take forever returns instantly
    assert validation.validate_dry_run("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c", con) == []


def test_schema_is_derived_from_schema_sql():
    assert set(validation.SCHEMA) == {"users", "groups", "group_members", "applications", "entitlements", "access_grants", "activity_events"}
    assert "mfa_enabled" in validation.SCHEMA["users"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))


def test_invalid_sql_is_not_executed_and_user_gets_reasons(env, monkeypatch, caplog):
    chats, executed = [], []

    async def chat(messages, *, schema=None, timeout=120):
        chats.append(schema is not None)
        return '{"sql": "SELECT i.integration FROM entitlements i"}'

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(pipeline, "execute", lambda sql: executed.append(sql))
    caplog.set_level(logging.INFO, logger="identity_mcp")
    out = run(pipeline.ask("apps per integration", k=0))

    assert executed == [] and chats == [True]  # never executed, and the summarizer was never called
    assert "rows" not in out and out["sql"] == "SELECT i.integration FROM entitlements i"
    assert out["validation_errors"] and "Unknown column: integration" in out["validation_errors"][0]
    assert out["error"].startswith("Query failed validation (static)")
    assert "I didn't run anything" in out["answer"] and "- Column check failed" in out["answer"]
    assert "validate" in out["timings_ms"] and "execute" not in out["timings_ms"]


def test_valid_sql_passes_through_unchanged(env, monkeypatch):
    async def chat(messages, *, schema=None, timeout=120):
        return '{"sql": "SELECT COUNT(*) FROM users"}' if schema else "500 users."

    monkeypatch.setattr(llm, "chat", chat)
    out = run(pipeline.ask("how many users?", k=0))
    assert out["rows"] == [[500]] and "validation_errors" not in out and "error" not in out
    assert "validate" in out["timings_ms"]
