"""Handing one tool's result to the next: the result store, the ``input`` table, and the dormant-admins chain."""

import asyncio
import json

import pytest
from mcp import Client

from identity_mcp import handoff, llm, mcp_server, pipeline
from identity_mcp.data import seed
from identity_mcp.handoff import HandoffError, Input, ResultStore
from identity_mcp.scopes import ACTIVITY

run = asyncio.run


# --- the store ---------------------------------------------------------------------------------------------

def test_values_come_back_distinct_and_in_order():
    store = ResultStore()
    rid = store.put(["id", "name"], [[3, "c"], [1, "a"], [3, "c"], [None, "x"], [2, "b"]])
    inp = store.values(rid, "ID")  # column names are case-insensitive
    assert inp.values == (3, 1, 2) and inp.column == "id" and inp.count == 3


def test_unknown_id_unknown_column_and_non_id_columns_explain_themselves():
    store = ResultStore()
    rid = store.put(["id", "score"], [[1, 0.5]])
    with pytest.raises(HandoffError, match="not found; it may have expired"):
        store.values("r_nope", "id")
    with pytest.raises(HandoffError, match="no column 'name'. Its columns are: id, score"):
        store.values(rid, "name")
    with pytest.raises(HandoffError, match="is not an id"):
        store.values(rid, "score")
    name_id = store.put(["id", "name"], [[1, "a"]])
    with pytest.raises(HandoffError, match="share a name"):
        store.values(name_id, "name")  # names repeat, so they cannot identify people


def test_too_many_values_are_refused():
    store = ResultStore()
    rid = store.put(["id"], [[i] for i in range(handoff.MAX_INPUT_VALUES + 1)])
    with pytest.raises(HandoffError, match="limit is 500"):
        store.values(rid, "id")


def test_which_columns_count_as_keys():
    assert all(handoff.is_key_column(c) for c in ("id", "ID", "user_id", "app_id", "email"))
    assert not any(handoff.is_key_column(c) for c in ("name", "department", "idea", "valid"))


def test_a_query_that_ignores_the_input_table_is_caught():
    inp = Input((1, 2), "id")
    assert handoff.unused_input("SELECT name FROM users", None) == []
    (msg,) = handoff.unused_input("SELECT name FROM users", inp)
    assert "ignores the input table" in msg and "2 ids" in msg
    assert handoff.unused_input("SELECT name FROM users WHERE id IN (SELECT value FROM input)", inp) == []
    assert handoff.unused_input("SELECT i.value FROM input i WHERE NOT EXISTS (SELECT 1 FROM users)", inp) == []
    assert handoff.unused_input("WITH t AS (SELECT 1) SELECT * FROM t JOIN input ON 1 = 1", inp) == []
    assert handoff.unused_input("SELEC broken", inp) == []  # syntax errors belong to validation


def test_entries_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(handoff.time, "monotonic", lambda: now[0])
    store = ResultStore(ttl=60)
    rid = store.put(["id"], [[1]])
    now[0] += 59
    assert store.values(rid, "id").values == (1,)
    now[0] += 2
    with pytest.raises(HandoffError, match="expired"):
        store.values(rid, "id")


def test_the_oldest_entries_go_first_beyond_capacity():
    store = ResultStore(capacity=2)
    first, second, third = (store.put(["id"], [[i]]) for i in range(3))
    with pytest.raises(HandoffError):
        store.values(first, "id")
    assert store.values(second, "id").values == (1,) and store.values(third, "id").values == (2,)


def test_stored_rows_are_copies():
    store = ResultStore()
    rows = [[1]]
    rid = store.put(["id"], rows)
    rows[0][0] = 99
    assert store.values(rid, "id").values == (1,)


# --- the input table ---------------------------------------------------------------------------------------

def test_with_input_prepends_or_merges_a_cte():
    inp = Input((1, 2), "id")
    assert handoff.with_input("SELECT 1", None) == "SELECT 1"
    assert handoff.with_input("SELECT value FROM input", inp) == "WITH input(value) AS (VALUES (1), (2)) SELECT value FROM input"
    assert handoff.with_input("WITH t AS (SELECT 1) SELECT * FROM t", inp).startswith("WITH input(value) AS (VALUES (1), (2)), t AS")
    assert handoff.with_input("with recursive t(n) AS (SELECT 1) SELECT n FROM t", inp).startswith(
        "WITH recursive input(value) AS (VALUES (1), (2)), t(n) AS")


def test_text_values_are_quoted_safely():
    sql = handoff.with_input("SELECT value FROM input", Input(("o'brien", "x); DROP TABLE users; --"), "email"))
    assert "('o''brien')" in sql and "('x); DROP TABLE users; --')" in sql  # a quote inside a value cannot end the literal


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))
    return tmp_path / "app.db"


def test_the_input_table_works_in_a_real_query(env):
    inp = Input((1, 2, 3), "id")
    rows = pipeline.execute("SELECT u.id FROM users u WHERE u.id IN (SELECT value FROM input) ORDER BY u.id", None, inp)["rows"]
    assert rows == [[1], [2], [3]]
    empty = pipeline.execute("SELECT COUNT(*) FROM input", None, Input((), "id"))["rows"]
    assert empty == [[0]]


def test_hostile_text_values_cannot_inject_sql(env):
    inp = Input(("1'); DROP TABLE users; --",), "email")
    assert pipeline.execute("SELECT COUNT(*) FROM input", None, inp)["rows"] == [[1]]
    assert pipeline.execute("SELECT COUNT(*) FROM users")["rows"] == [[500]]


# --- the chain through the two MCP tools -------------------------------------------------------------------

ADMINS = """SELECT DISTINCT u.id, u.name FROM users u JOIN access_grants g ON g.user_id = u.id
JOIN entitlements e ON e.id = g.entitlement_id WHERE e.is_admin = 1 AND u.status = 'active'"""
NO_ACTIVITY = """SELECT u.id, u.name FROM input i JOIN users u ON u.id = i.value
WHERE NOT EXISTS (SELECT 1 FROM activity_events ev WHERE ev.user_id = i.value AND ev.ts >= date('2026-11-01', '-90 days'))
ORDER BY u.id"""


def scripted(*sqls):
    """Fake model that answers each SQL-generation call with the next query in turn."""
    queue = list(sqls)

    async def chat(messages, *, schema=None, timeout=120):
        return json.dumps({"sql": queue.pop(0)}) if schema else "unused"
    return chat


def test_dormant_admins_by_chaining_the_two_tools(env, monkeypatch):
    monkeypatch.setattr(llm, "chat", scripted(ADMINS, NO_ACTIVITY))

    async def go():
        async with Client(mcp_server.mcp) as client:
            first = (await client.call_tool("query_identity", {"question": "active users with admin access"})).structured_content
            second = (await client.call_tool("query_activity", {
                "question": "which of these users had no activity in the last 90 days?",
                "filter_from": {"result_id": first["result_id"], "column": "id"},
            })).structured_content
            return first, second

    first, second = run(go())
    expected = pipeline.execute(
        f"WITH admins AS ({ADMINS}) SELECT a.id, a.name FROM admins a WHERE NOT EXISTS "
        "(SELECT 1 FROM activity_events ev WHERE ev.user_id = a.id AND ev.ts >= date('2026-11-01', '-90 days')) ORDER BY a.id"
    )["rows"]
    assert second["input_count"] == first["row_count"] and second["scope"] == "activity"
    assert second["rows"] == expected[: mcp_server.SHOWN_ROWS] and second["row_count"] == len(expected) and expected  # nonempty


def test_a_second_step_that_ignores_the_ids_is_sent_back_for_repair(env, monkeypatch):
    calls = []
    ignores = "SELECT u.id, u.name FROM users u WHERE NOT EXISTS (SELECT 1 FROM activity_events ev WHERE ev.user_id = u.id)"

    async def chat(messages, *, schema=None, timeout=120):
        calls.append(messages)
        return json.dumps({"sql": ignores if len(calls) == 1 else NO_ACTIVITY})

    monkeypatch.setattr(llm, "chat", chat)
    out = run(pipeline.ask("which of these had no activity?", scope=ACTIVITY, inputs=Input((1, 2, 3), "id"), summarize_answer=False, k=0))
    assert out["repairs"] == 1 and out["attempts"][0]["stage"] == "input" and out["sql"] == NO_ACTIVITY
    assert {r[0] for r in out["rows"]} <= {1, 2, 3}  # only the handed-over ids can come back
    assert "ignores the input table" in calls[1][-1]["content"]


def test_the_second_tool_reports_a_bad_handle_instead_of_guessing(env, monkeypatch):
    monkeypatch.setattr(llm, "chat", scripted())  # must not even reach the model

    async def go():
        async with Client(mcp_server.mcp) as client:
            return (await client.call_tool("query_activity", {
                "question": "anything", "filter_from": {"result_id": "r_gone", "column": "id"}})).structured_content

    out = run(go())
    assert "not found; it may have expired" in out["error"] and "sql" not in out


def test_a_failed_first_step_has_no_result_id(env, monkeypatch):
    monkeypatch.setattr(llm, "chat", scripted("SELECT * FROM activity_events", "SELECT * FROM activity_events", "SELECT * FROM activity_events"))

    async def go():
        async with Client(mcp_server.mcp) as client:
            return (await client.call_tool("query_identity", {"question": "events please"})).structured_content

    out = run(go())
    assert "not available in the identity tool" in out["error"] and "result_id" not in out
