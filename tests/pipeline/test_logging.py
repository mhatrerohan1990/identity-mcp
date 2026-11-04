import asyncio
import logging

import pytest

from identity_mcp.core import llm, logs
from identity_mcp import pipeline
from identity_mcp.data import seed

run = asyncio.run


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))
    sent = []

    async def post(path, body, timeout):  # the HTTP layer under llm.chat
        sent.append(body)
        if body.get("format"):
            return {"message": {"content": '{"sql": "SELECT COUNT(*) FROM users WHERE status = \'terminated\'"}'}}
        return {"message": {"content": "50 users are terminated."}}

    monkeypatch.setattr(llm, "_post", post)
    return sent


def test_flow_and_full_prompts_are_logged(env, caplog):
    caplog.set_level(logging.DEBUG, logger="identity_mcp")
    out = run(pipeline.ask("Which terminated users still have access?", k=2))
    assert out["answer"] == "50 users are terminated."

    flow = [r.getMessage() for r in caplog.records if r.name == "identity_mcp.flow"]
    steps = ["ask start", "retrieved 2 example(s)", "generated sql", "executed in", "summarized", "ask done"]
    positions = [next(i for i, m in enumerate(flow) if m.startswith(s)) for s in steps]
    assert positions == sorted(positions), flow  # steps appear in pipeline order
    by_step = {step: flow[i] for step, i in zip(steps, positions)}
    assert "ex-0" in by_step["retrieved 2 example(s)"] and "SELECT COUNT(*) FROM users" in by_step["generated sql"]
    assert "1 row(s)" in by_step["executed in"]
    assert any(m.startswith("embedding 104 of 104") for m in flow)  # cold cache: bank embedded on first use

    prompts = [r.getMessage() for r in caplog.records if r.name == "identity_mcp.prompt"]
    assert len(prompts) == 4  # request + response for the SQL call, request + response for the summary call
    sql_request, sql_response, answer_request, answer_response = prompts
    # exactly what was sent: schema, retrieved examples, then the user's question
    assert "CREATE TABLE users" in sql_request and "Worked examples" in sql_request
    assert sql_request.rstrip().endswith(f"Which terminated users still have access?\n===== end LLM request model={llm.MODEL} structured=yes =====")
    assert "SELECT COUNT(*) FROM users WHERE status" in sql_response
    assert "Query results (1 rows)" in answer_request and "[[50]]" in answer_request
    assert "50 users are terminated." in answer_response

    # the logged system prompt is byte-for-byte what went over the wire
    wire_system = env[0]["messages"][0]["content"]
    assert wire_system in sql_request


def test_every_record_of_one_ask_shares_a_request_id(env, caplog):
    logs.configure()  # installs the filter that adds request_id to the handler's records
    handler = next(h for h in logging.getLogger("identity_mcp").handlers if getattr(h, "_identity_mcp", False))
    seen = []
    handler.emit = lambda record: seen.append(handler.format(record))  # capture formatted lines, no stderr noise
    try:
        caplog.set_level(logging.DEBUG, logger="identity_mcp")
        run(pipeline.ask("how many?", k=1))
        run(pipeline.ask("and again?", k=1))
    finally:
        del handler.emit
    ids = [line.split("[")[1].split("]")[0] for line in seen if "[" in line.split("|")[0]]
    assert len(set(ids)) == 2 and ids[0] != "-"
    first = ids[0]
    assert ids.count(first) == ids.index(ids[-1]) and all(i == first for i in ids[: ids.count(first)])


def test_prompts_can_be_switched_off(env, caplog, monkeypatch):
    monkeypatch.setenv("IDENTITY_MCP_LOG_PROMPTS", "0")
    monkeypatch.setenv("IDENTITY_MCP_LOG_LEVEL", "INFO")
    logs.configure()
    try:
        run(pipeline.ask("how many?", k=1))
        assert not [r for r in caplog.records if r.name == "identity_mcp.prompt"]
        assert [r for r in caplog.records if r.name == "identity_mcp.flow"]
    finally:
        monkeypatch.setenv("IDENTITY_MCP_LOG_PROMPTS", "1")
        logs.configure()


def test_configure_is_idempotent():
    logs.configure()
    logs.configure()
    handlers = [h for h in logging.getLogger("identity_mcp").handlers if getattr(h, "_identity_mcp", False)]
    assert len(handlers) == 1


def test_failures_are_logged_with_the_sql(env, caplog, monkeypatch):
    async def post(path, body, timeout):
        return {"message": {"content": '{"sql": "SELECT nope FROM users"}'}} if body.get("format") else {"message": {"content": "x"}}

    monkeypatch.setattr(llm, "_post", post)
    caplog.set_level(logging.INFO, logger="identity_mcp")
    out = run(pipeline.ask("q", k=0))
    assert "error" in out
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "nope does not exist" in errors[0] and "SELECT nope FROM users" in errors[0]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(m.startswith("validation failed at static stage") for m in warnings)
