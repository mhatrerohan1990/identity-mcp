"""The gaps found by probing the validator: false rejections, bad messages, resource abuse, unvalidated paths."""

import sqlite3

import pytest

from identity_mcp import pipeline, validation
from identity_mcp.data import seed


@pytest.fixture(scope="module")
def dbpath(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    return path


@pytest.fixture
def env(dbpath, monkeypatch):
    monkeypatch.setenv("IDENTITY_MCP_DB", str(dbpath))


@pytest.fixture
def con(env):
    con = validation.connect_query()
    yield con
    con.close()


# ---- false rejections ------------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM users; -- how many",
    "SELECT COUNT(*) FROM users;",
    "SELECT COUNT(*) FROM users;;",
    "/* leading */ SELECT COUNT(*) FROM users -- trailing",
])
def test_comments_and_semicolons_are_not_extra_statements(con, sql):
    assert validation.validate(sql, con).ok


def test_real_second_statement_is_still_rejected():
    assert validation.validate_static("SELECT 1; SELECT 2") == ["Exactly one statement is allowed, found 2."]


# ---- messages the model can act on ------------------------------------------------------------------
def test_ambiguous_column_is_called_ambiguous():
    (msg,) = validation.validate_static("SELECT id FROM users u JOIN groups g ON g.id = u.id")
    assert "'id' is ambiguous" in msg and "groups, users" in msg and "u.id" in msg and "g.id" in msg
    assert "does not exist" not in msg


def test_double_quoted_value_gets_the_single_quote_hint():
    (msg,) = validation.validate_static('SELECT COUNT(*) FROM users WHERE status = "terminated"')
    assert "Text values must use single quotes: 'terminated'" in msg
    # a double-quoted *identifier* that is real stays valid
    assert validation.validate_static('SELECT "name" FROM users WHERE "status" = \'active\'') == []


def test_qualifying_the_ambiguous_column_fixes_it():
    assert validation.validate_static("SELECT u.id FROM users u JOIN groups g ON g.id = u.id") == []


# ---- cartesian products -----------------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM activity_events a, activity_events b",
    "SELECT COUNT(*) FROM activity_events a CROSS JOIN activity_events b",
    "SELECT COUNT(*) FROM activity_events a JOIN activity_events b",
    "SELECT u.name FROM users u, groups g WHERE u.status = 'active'",  # a WHERE that does not connect the tables
])
def test_joins_without_a_condition_are_rejected(sql):
    (msg,) = validation.validate_static(sql)
    assert "no join condition" in msg and "cartesian product" in msg


@pytest.mark.parametrize("sql", [
    "SELECT u.name FROM users u JOIN group_members m ON m.user_id = u.id",
    "SELECT u.name FROM users u, group_members m WHERE m.user_id = u.id AND u.status = 'active'",  # old-style join
    "SELECT u.name FROM users u JOIN group_members m USING (user_id)",
    "SELECT u.name FROM users u LEFT JOIN users m ON m.id = u.manager_id",
])
def test_legitimate_joins_pass(sql):
    errors = validation.validate_static(sql)
    assert not any("join condition" in e for e in errors), errors


# ---- functions and size limits ----------------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "SELECT length(randomblob(1000000000))",
    "SELECT length(zeroblob(1000000000))",
    "SELECT printf('%1000000000d', 1)",
    "SELECT quote(name) FROM users",
])
def test_unlisted_functions_are_rejected_statically(sql):
    (msg,) = validation.validate_static(sql)
    assert "is not allowed" in msg


@pytest.mark.parametrize("sql, fn", [
    ("SELECT hex(name) FROM users", "hex"),               # sqlglot knows hex(), so only SQLite's own check sees it
    ("SELECT format('%5d', 1)", "format"),
])
def test_the_sandbox_denies_functions_the_static_check_does_not_name(con, sql, fn):
    assert validation.validate_static(sql) == []
    errors = validation.validate_dry_run(sql, con)
    assert errors and f"not authorized to use function: {fn}" in errors[0]


def test_the_sandbox_cannot_read_other_tables_or_write(con):
    for sql in ["SELECT name FROM sqlite_master", "DELETE FROM users", "PRAGMA table_info(users)", "ATTACH DATABASE ':memory:' AS x"]:
        with pytest.raises(sqlite3.Error):
            con.execute(sql).fetchall()
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 500  # known tables still readable


def test_recursive_queries_over_their_own_name_work(env):
    org_chart = """WITH RECURSIVE chain(id, depth) AS (
        SELECT id, 0 FROM users WHERE manager_id IS NULL
        UNION ALL SELECT u.id, chain.depth + 1 FROM users u JOIN chain ON u.manager_id = chain.id)
        SELECT MAX(depth) AS levels FROM chain"""
    assert validation.validate(org_chart).ok
    assert pipeline.execute(org_chart)["rows"][0][0] >= 1


def test_engine_limit_stops_runaway_values(env):
    # built from allowed functions only (string doubling), so only the engine limit can stop it
    sql = "WITH RECURSIVE r(s) AS (SELECT 'x' UNION ALL SELECT s || s FROM r WHERE length(s) < 5000000) SELECT length(s) FROM r"
    assert validation.validate_static(sql) == []
    with pytest.raises(pipeline.QueryError, match="larger than the 1 MB limit"):
        pipeline.execute(sql)


def test_length_cap():
    (msg,) = validation.validate_static("SELECT " + ", ".join(["1"] * 3000))
    assert "too long" in msg


# ---- robustness -------------------------------------------------------------------------------------
def test_deep_nesting_is_rejected_not_a_crash():
    sql = "SELECT " + "(" * 1800 + "1" + ")" * 1800
    result = validation.validate(sql)
    assert not result.ok and result.stage == "static"


def test_a_crash_inside_the_checker_fails_closed(monkeypatch):
    def boom(sql):
        raise KeyError("unexpected")

    monkeypatch.setattr(validation, "validate_static", boom)
    result = validation.validate("SELECT 1")
    assert not result.ok and "could not be analyzed (KeyError)" in result.errors[0]


# ---- the executor cannot be handed unvalidated SQL ---------------------------------------------------
def test_execute_refuses_unvalidated_sql(env):
    for sql in ["DELETE FROM users", "SELECT * FROM employees", "SELECT 1; SELECT 2", "SELECT length(randomblob(1000000000))"]:
        with pytest.raises(validation.ValidationError):
            pipeline.execute(sql)


def test_timeouts_are_explained(env, monkeypatch):
    monkeypatch.setattr(pipeline, "QUERY_TIMEOUT_S", 0.3)
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(pipeline.QueryError, match="ran longer than 0.3 seconds"):
        pipeline.execute(sql)


def test_ask_reports_runtime_failures_in_plain_language(env, monkeypatch):
    import asyncio
    from identity_mcp import llm

    monkeypatch.setattr(pipeline, "QUERY_TIMEOUT_S", 0.3)
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"

    async def chat(messages, *, schema=None, timeout=120):
        return '{"sql": "%s"}' % slow if schema else "n/a"

    monkeypatch.setattr(llm, "chat", chat)
    out = asyncio.run(pipeline.ask("q", k=0))
    assert "ran longer than 0.3 seconds" in out["error"] and out["answer"].startswith("The query couldn't be completed")
