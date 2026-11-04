import asyncio
import json

import pytest

from identity_mcp.core import llm
from identity_mcp import pipeline
from identity_mcp.data import seed
from identity_mcp.pipeline import engine

run = asyncio.run
BAD = "SELECT i.integration, COUNT(*) FROM entitlements i GROUP BY i.integration"
GOOD = "SELECT integration, COUNT(*) AS apps FROM applications GROUP BY integration"


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))


def scripted(sqls, calls):
    """Fake model: replies with each query in turn (repeating the last), records every conversation it is sent."""
    async def chat(messages, *, schema=None, timeout=120):
        if not schema:
            return "summary"
        calls.append(messages)
        return json.dumps({"sql": sqls[min(len(calls) - 1, len(sqls) - 1)]})
    return chat


def test_invalid_query_is_repaired_then_executed(env, monkeypatch):
    calls, executed = [], []
    real_execute = pipeline.execute
    monkeypatch.setattr(engine, "execute", lambda sql, *a: (executed.append(sql), real_execute(sql, *a))[1])
    monkeypatch.setattr(llm, "chat", scripted([BAD, GOOD], calls))
    out = run(pipeline.ask("apps per integration", k=0))

    assert out["sql"] == GOOD and out["repairs"] == 1 and "error" not in out and "validation_errors" not in out
    assert executed == [GOOD]  # the invalid query never ran
    assert [a["ok"] for a in out["attempts"]] == [False, True]
    assert out["attempts"][0]["sql"] == BAD and "i.integration (i is entitlements)" in out["attempts"][0]["errors"][0]
    assert out["rows"] and out["answer"] == "summary" and "repair" in out["timings_ms"]


def test_repair_conversation_shows_the_model_its_mistake(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted([BAD, GOOD], calls))
    run(pipeline.ask("apps per integration", k=0))
    first, second = calls
    assert len(first) == 2  # system + user
    assert second[:2] == first  # same system prompt and question
    assert json.loads(second[2]["content"]) == {"sql": BAD} and second[2]["role"] == "assistant"  # its own reply
    feedback = second[3]
    assert feedback["role"] == "user" and "failed validation and was not run" in feedback["content"]
    assert "i.integration (i is entitlements) does not exist" in feedback["content"]
    assert "'integration' exists on: applications" in feedback["content"]  # the column is real, just on another table


def test_stops_after_the_repair_limit_and_reports_the_last_failure(env, monkeypatch):
    calls = []
    sqls = [f"SELECT bad{i} FROM users" for i in range(5)]  # always invalid, always different
    monkeypatch.setattr(llm, "chat", scripted(sqls, calls))
    out = run(pipeline.ask("q", k=0))
    assert len(calls) == 3 == 1 + pipeline.DEFAULT_MAX_REPAIRS  # first try + 2 repairs, then give up
    assert out["repairs"] == 2 and len(out["attempts"]) == 3 and out["sql"] == sqls[2]
    assert "bad2" in out["validation_errors"][0] and "after 2 repair attempt(s)" in out["answer"] and "rows" not in out
    assert len(calls[2]) == 2 + 2 * 2  # the conversation grew by one failed reply and one feedback per attempt


def test_limit_is_configurable(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted([f"SELECT bad{i} FROM users" for i in range(9)], calls))
    monkeypatch.setenv("IDENTITY_MCP_MAX_REPAIRS", "1")
    assert run(pipeline.ask("q", k=0))["repairs"] == 1 and len(calls) == 2
    calls.clear()
    monkeypatch.setenv("IDENTITY_MCP_MAX_REPAIRS", "0")
    assert run(pipeline.ask("q", k=0))["repairs"] == 0 and len(calls) == 1
    monkeypatch.setenv("IDENTITY_MCP_MAX_REPAIRS", "nonsense")
    assert pipeline.max_repairs() == pipeline.DEFAULT_MAX_REPAIRS


def test_repeating_the_same_query_ends_the_loop_early(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted([BAD], calls))  # model keeps returning the same invalid query
    out = run(pipeline.ask("q", k=0))
    assert len(calls) == 2 and "error" in out  # one repair attempt, no point asking a third time
    assert out["repairs"] == 1 and len(out["attempts"]) == 1  # the retry was made, but added no new query


def test_valid_first_try_costs_nothing_extra(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted([GOOD], calls))
    out = run(pipeline.ask("q", k=0))
    assert len(calls) == 1 and out["repairs"] == 0 and len(out["attempts"]) == 1 and "repair" not in out["timings_ms"]


def test_dry_run_failures_are_repaired_too(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted(["SELECT substr(name) FROM users", "SELECT substr(name, 1, 3) FROM users"], calls))
    out = run(pipeline.ask("q", k=0))
    assert out["repairs"] == 1 and out["attempts"][0]["stage"] == "dry_run"
    assert "wrong number of arguments to function substr()" in calls[1][3]["content"]


def test_a_request_to_change_data_is_refused_before_any_model_call(monkeypatch):
    import asyncio

    from identity_mcp import pipeline
    from identity_mcp.core import llm
    from identity_mcp.rag import retrieval

    async def boom(*args, **kwargs):
        raise AssertionError("no model, retrieval or query may run for a refused request")

    monkeypatch.setattr(llm, "chat", boom)
    monkeypatch.setattr(llm, "embed", boom)
    monkeypatch.setattr(retrieval, "retrieve", boom)
    out = asyncio.run(pipeline.ask("can you delete all admins"))
    assert out["refused"] is True and "read-only" in out["answer"] and "error" not in out and out["sql"] is None
