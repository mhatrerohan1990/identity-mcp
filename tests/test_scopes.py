"""Scopes: each tool sees only its own tables, enforced in the prompt, in validation and in the SQLite sandbox."""

import asyncio
import json
import sqlite3

import pytest

from identity_mcp import bank, llm, pipeline, validation
from identity_mcp.data import seed
from identity_mcp.scopes import ACTIVITY, IDENTITY, SCOPES, tables_in

run = asyncio.run


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))


def test_the_two_scopes_cover_every_table_between_them():
    assert set(SCOPES) == {"identity", "activity"}
    assert IDENTITY.tables | ACTIVITY.tables == set(validation.SCHEMA)
    assert "activity_events" not in IDENTITY.tables and "access_grants" not in ACTIVITY.tables


def test_tables_in_matches_whole_words_only():
    assert tables_in("SELECT group_id FROM group_members g JOIN users u ON u.id = g.user_id") == {"group_members", "users"}
    assert tables_in("SELECT 1") == frozenset()


def test_schema_text_holds_only_the_scopes_tables_and_indexes():
    identity, activity = IDENTITY.schema_sql(), ACTIVITY.schema_sql()
    assert "CREATE TABLE access_grants" in identity and "CREATE TABLE activity_events" not in identity
    assert "idx_events" not in identity and "idx_grants_user" in identity
    assert "CREATE TABLE activity_events" in activity and "CREATE TABLE users" in activity
    assert "access_grants" not in activity and "idx_grants" not in activity and "idx_events_user_ts" in activity
    assert "-- ISO-8601 date" in identity  # the schema's comments survive the slicing


def test_bank_examples_sort_into_scopes():
    ex = bank.load()
    identity = [e.id for e in ex if IDENTITY.allows(e.sql)]
    activity = [e.id for e in ex if ACTIVITY.allows(e.sql)]
    assert len(identity) >= 70 and len(activity) >= 40
    both = {e.id for e in ex} - set(identity) - set(activity)
    assert {"ex-068", "ex-069"} <= both  # dormant admins need both sides, so no single scope has them


@pytest.mark.parametrize("scope, sql, table", [
    (IDENTITY, "SELECT * FROM activity_events", "activity_events"),
    (IDENTITY, "SELECT u.name FROM users u JOIN activity_events e ON e.user_id = u.id", "activity_events"),
    (ACTIVITY, "SELECT * FROM access_grants", "access_grants"),
    (ACTIVITY, "SELECT * FROM groups", "groups"),
])
def test_static_check_rejects_tables_outside_the_scope(scope, sql, table):
    (msg,) = validation.validate_static(sql, scope)
    assert f"Table '{table}' is not available in the {scope.name} tool" in msg
    assert validation.validate_static(sql) == []  # without a scope it is fine


def test_in_scope_queries_and_ctes_pass():
    assert validation.validate_static("SELECT * FROM users u JOIN access_grants g ON g.user_id = u.id", IDENTITY) == []
    assert validation.validate_static("WITH t AS (SELECT user_id FROM activity_events) SELECT * FROM t", ACTIVITY) == []


def test_sqlite_itself_refuses_out_of_scope_tables(env):
    con = validation.connect_query(IDENTITY)  # skipping the static check entirely
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        con.execute("SELECT COUNT(*) FROM activity_events").fetchall()
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 500
    assert validation.connect_query(None).execute("SELECT COUNT(*) FROM activity_events").fetchone()[0] > 0


def test_dry_run_respects_the_scope(env):
    assert validation.validate("SELECT COUNT(*) FROM activity_events").ok
    result = validation.validate("SELECT COUNT(*) FROM activity_events", scope=IDENTITY)
    assert not result.ok and result.stage == "static"


def test_execute_with_a_scope(env):
    assert pipeline.execute("SELECT COUNT(*) FROM users", IDENTITY)["rows"] == [[500]]
    with pytest.raises(pipeline.ValidationError, match="not available in the identity tool"):
        pipeline.execute("SELECT COUNT(*) FROM activity_events", IDENTITY)


def capture(sql, calls):
    async def chat(messages, *, schema=None, timeout=120):
        calls.append((schema, messages))
        return json.dumps({"sql": sql}) if schema else "summary"
    return chat


def test_scoped_prompt_shows_only_the_scopes_schema_and_examples(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", capture("SELECT COUNT(*) FROM activity_events", calls))
    out = run(pipeline.ask("how many events happened?", scope=ACTIVITY, k=4))
    system = next(m for s, m in calls if s is pipeline.SQL_SCHEMA)[0]["content"]
    assert "CREATE TABLE activity_events" in system and "CREATE TABLE access_grants" not in system
    assert "entitlements.is_admin" not in system and "event_type is login" in system  # scope-specific rules
    by_id = {e.id: e for e in bank.load()}
    assert out["examples"] and all(ACTIVITY.allows(by_id[e["id"]].sql) for e in out["examples"])
    assert out["scope"] == "activity" and out["rows"]


def test_out_of_scope_query_is_repaired_then_reported(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", capture("SELECT COUNT(*) FROM activity_events", calls))
    out = run(pipeline.ask("how many events happened?", scope=IDENTITY, k=0))
    assert "error" in out and "not available in the identity tool" in out["error"] and "rows" not in out
    assert out["attempts"][0]["stage"] == "static"


def test_summary_can_be_skipped(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", capture("SELECT COUNT(*) FROM users", calls))
    out = run(pipeline.ask("how many users?", summarize_answer=False, k=0))
    assert out["answer"] == "" and out["rows"] == [[500]] and "summarize" not in out["timings_ms"]
    assert [s is None for s, _ in calls].count(True) == 0  # no unstructured (summary) call was made
