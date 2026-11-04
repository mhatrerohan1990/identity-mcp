import asyncio

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from identity_mcp.core import llm
from identity_mcp import mcp_server, pipeline
from identity_mcp.api.app import app
from identity_mcp.data import seed


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "app.db"
    seed.build(path)
    mp = pytest.MonkeyPatch()
    mp.setenv("IDENTITY_MCP_DB", str(path))
    yield path
    mp.undo()


def fake_llm(sql: str, answer: str = "ok"):
    """Stand-in for Ollama: structured calls (SQL generation) get `sql`, others get `answer`."""
    async def chat(messages, *, schema=None, timeout=120):
        return '{"sql": "%s"}' % sql if schema else answer
    return chat


def run(coro):
    return asyncio.run(coro)


def test_pipeline_happy_path(db, monkeypatch):
    monkeypatch.setattr(llm, "chat", fake_llm("SELECT COUNT(*) FROM users WHERE status = 'terminated'", "50 are terminated."))
    out = run(pipeline.ask("how many terminated?"))
    assert out["sql"].startswith("SELECT COUNT") and out["rows"] == [[50]] and out["answer"] == "50 are terminated."
    assert "error" not in out


def test_pipeline_strips_code_fences_and_semicolon(db, monkeypatch):
    async def chat(messages, *, schema=None, timeout=120):
        return '{"sql": "```sql\\nSELECT 1;\\n```"}' if schema else "one"
    monkeypatch.setattr(llm, "chat", chat)
    assert run(pipeline.ask("x"))["sql"] == "SELECT 1"


def test_pipeline_zero_rows_skips_summary_llm(db, monkeypatch):
    monkeypatch.setattr(llm, "chat", fake_llm("SELECT * FROM users WHERE id < 0", "SHOULD NOT BE USED"))
    out = run(pipeline.ask("mars?"))
    assert out["row_count"] == 0 and out["answer"] == "No matching records were found."


def test_pipeline_bad_sql_is_reported_not_raised(db, monkeypatch):
    monkeypatch.setattr(llm, "chat", fake_llm("SELECT nope FROM users"))
    out = run(pipeline.ask("x"))
    assert "nope does not exist" in out["error"] and out["sql"] == "SELECT nope FROM users"
    assert out["validation_errors"] and "rows" not in out  # rejected before execution


def test_execution_is_read_only(db):
    with pytest.raises(pipeline.ValidationError, match="Only SELECT"):
        pipeline.execute("DELETE FROM users")  # refused before it reaches the database
    assert pipeline.execute("SELECT COUNT(*) FROM users")["rows"] == [[500]]


def test_row_cap(db):
    out = pipeline.execute("SELECT * FROM activity_events")
    assert out["row_count"] == pipeline.MAX_ROWS and out["truncated"]


def test_llm_down_is_reported(db, monkeypatch):
    async def chat(*a, **k):
        raise llm.LLMError("Cannot reach the LLM")
    monkeypatch.setattr(llm, "chat", chat)
    out = run(pipeline.ask("x"))
    assert "Cannot reach" in out["error"]


def test_mcp_exposes_the_two_scoped_tools(db, monkeypatch):
    monkeypatch.setattr(llm, "chat", fake_llm("SELECT COUNT(*) FROM groups"))

    async def go():
        async with Client(mcp_server.mcp) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            result = await client.call_tool("query_identity", {"question": "how many groups?"})
            return tools, result.structured_content

    tools, out = run(go())
    assert list(tools) == ["query_identity", "query_activity"]
    for tool in tools.values():
        assert "filter_from" in tool.input_schema["properties"] and tool.input_schema["required"] == ["question"]
    assert "query_activity" in tools["query_identity"].description and "query_identity" in tools["query_activity"].description
    assert out["scope"] == "identity" and out["rows"] == [[12]] and out["result_id"].startswith("r_") and "answer" not in out


def test_mcp_mounted_in_fastapi(db):
    with TestClient(app, base_url="http://localhost:8000") as client:
        r = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        )
        assert r.status_code == 200 and "identity-mcp" in r.text
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/users", params={"limit": 1}).status_code == 200


def test_chat_page_and_ask_endpoint(db, monkeypatch):
    monkeypatch.setattr(llm, "chat", fake_llm("SELECT COUNT(*) FROM groups", "12 groups."))
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "identity-mcp" in page.text
    r = client.post("/api/ask", json={"question": "  how many groups?  "})
    assert r.status_code == 200 and r.json()["answer"] == "12 groups." and r.json()["rows"] == [[12]]
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
