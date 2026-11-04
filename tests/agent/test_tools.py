"""The MCP bridge: schema flattening, and calling the real tools through it (against the server in-process)."""

import asyncio
import json

import pytest
from mcp import Client

from identity_mcp import mcp_server
from identity_mcp.agent import tools as agent_tools
from identity_mcp.core import llm
from identity_mcp.data import seed

run = asyncio.run


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))


def server_schema():
    async def go():
        async with Client(mcp_server.mcp) as client:
            return (await client.list_tools()).tools[0].input_schema
    return run(go())


def test_flatten_schema_inlines_refs_and_drops_the_null_alternative():
    raw = server_schema()
    assert "$defs" in raw and "anyOf" in raw["properties"]["filter_from"]  # the shape that confuses small models
    flat = agent_tools.flatten_schema(raw)
    text = json.dumps(flat)
    for noise in ("$defs", "$ref", "anyOf", "null", "title"):
        assert noise not in text, noise
    link = flat["properties"]["filter_from"]
    assert link["type"] == "object" and set(link["properties"]) == {"result_id", "column"} and link["required"] == ["result_id", "column"]
    assert flat["required"] == ["question"]


def test_flatten_schema_leaves_a_simple_schema_alone():
    simple = {"type": "object", "properties": {"n": {"type": "integer", "description": "a number"}}, "required": ["n"]}
    assert agent_tools.flatten_schema(simple) == simple


def test_server_target_defaults_to_a_stdio_subprocess_and_honours_the_url(monkeypatch):
    monkeypatch.delenv(agent_tools.SERVER_ENV, raising=False)
    monkeypatch.setenv("IDENTITY_MCP_DB", "/tmp/somewhere.db")
    monkeypatch.setenv("UNRELATED_SECRET", "x")
    target = agent_tools.server_target()
    assert target.args == ["-m", "identity_mcp.mcp_server"] and target.env["IDENTITY_MCP_DB"] == "/tmp/somewhere.db"
    assert "UNRELATED_SECRET" not in target.env  # only the project's settings reach the subprocess
    monkeypatch.setenv(agent_tools.SERVER_ENV, "http://localhost:8000/mcp")
    assert agent_tools.server_target() == "http://localhost:8000/mcp"


def test_the_bridged_tools_call_the_real_server(env, monkeypatch):
    async def chat(messages, *, schema=None, timeout=120):
        return json.dumps({"sql": "SELECT id, name FROM users ORDER BY id LIMIT 3"})
    monkeypatch.setattr(llm, "chat", chat)

    async def go():
        async with agent_tools.open_tools(mcp_server.mcp) as tools:
            by_name = {t.name: t for t in tools}
            assert set(by_name) == {"query_identity", "query_activity"}
            assert "filter_from" in by_name["query_identity"].args
            first = json.loads(await by_name["query_identity"].ainvoke({"question": "first three users"}))
            bad = json.loads(await by_name["query_activity"].ainvoke(
                {"question": "anything", "filter_from": {"result_id": "r_gone", "column": "id"}}))
            return first, bad

    first, bad = run(go())
    assert first["scope"] == "identity" and first["row_count"] == 3 and first["result_id"].startswith("r_")
    assert "not found; it may have expired" in bad["error"]  # a tool-level problem reaches the model as data


def test_prune_removes_the_placeholders_models_send_for_not_given():
    assert agent_tools.prune({"question": "q", "filter_from": {"result_id": None, "column": None}}) == {"question": "q"}
    assert agent_tools.prune({"question": "q", "filter_from": {"result_id": "r_1", "column": None}}) == {
        "question": "q", "filter_from": {"result_id": "r_1"}}
    assert agent_tools.prune({"question": "q", "filter_from": {"result_id": "r_1", "column": "id"}}) == {
        "question": "q", "filter_from": {"result_id": "r_1", "column": "id"}}
    assert agent_tools.prune({"question": "q", "filter_from": "", "extra": [None, "x"]}) == {"question": "q", "extra": ["x"]}
    assert agent_tools.prune({"n": 0, "flag": False}) == {"n": 0, "flag": False}  # zero and False are real values
