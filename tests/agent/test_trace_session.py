"""Events for the screen (trace), chat history in the graph, and the streamed session: scripted model, fake tools."""

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from identity_mcp.agent import graph as agent_graph
from identity_mcp.agent import session
from identity_mcp.agent.trace import events_for, result_event

from test_graph import ScriptedModel, call, fake_tools, final


def test_a_tool_call_becomes_an_event_and_final_answer_does_not():
    update = {"messages": [call("query_activity", question="who was quiet?", filter_from={"result_id": "r_1", "column": "id"})]}
    assert events_for("agent", update) == [
        {"type": "tool_call", "name": "query_activity", "question": "who was quiet?", "filter_from": {"result_id": "r_1", "column": "id"}}]
    assert events_for("agent", {"messages": [final("done")]}) == []


def test_an_empty_filter_from_is_none():
    update = {"messages": [call("query_identity", question="q", filter_from={"result_id": None, "column": None})]}
    assert events_for("agent", update)[0]["filter_from"] is None


def test_a_tool_result_carries_rows_sql_and_errors():
    ok = ToolMessage(json.dumps({"scope": "identity", "row_count": 2, "columns": ["id"], "rows": [[1], [2]], "sql": "SELECT 1", "result_id": "r_1"}),
                     tool_call_id="c", name="query_identity")
    event = result_event(ok)
    assert (event["row_count"], event["rows"], event["sql"], event["error"]) == (2, [[1], [2]], "SELECT 1", None)
    bad = ToolMessage("Error: boom", tool_call_id="c", name="query_identity")
    assert result_event(bad)["error"] == "Error: boom"
    assert result_event(ToolMessage(json.dumps({"error": "no such table"}), tool_call_id="c", name="x"))["error"] == "no such table"


def test_a_nudge_is_an_event():
    assert events_for("nudge", {"messages": [HumanMessage("use a tool")]}) == [{"type": "nudge"}]


def test_history_is_shown_to_the_model_as_earlier_messages():
    state = agent_graph.initial_state("more on him", [("who is Arjun?", "Arjun Cohen, Sales")])
    kinds = [(type(m).__name__, m.content) for m in state["messages"]]
    assert kinds == [("HumanMessage", "who is Arjun?"), ("AIMessage", "Arjun Cohen, Sales"), ("HumanMessage", "more on him")]


def collect(question="q", history=(), replies=None, tools=None):
    model = ScriptedModel(replies=replies)
    async def go():
        out = []
        async for event in session.events(question, history, model=model, tools_target=None):
            out.append(event)
        return out
    return asyncio.run(go()), model


def patch_tools(monkeypatch, log=None):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_open(target=None):
        yield fake_tools(log if log is not None else [])
    monkeypatch.setattr(session, "open_tools", fake_open)


def test_session_streams_steps_then_the_answer(monkeypatch):
    patch_tools(monkeypatch)
    events, _ = collect(replies=[call("query_identity", question="admins"), final("Three admins.")])
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "answer"]
    assert events[-1]["text"] == "Three admins." and events[-1]["steps"] == 2
    assert events[1]["row_count"] == 3


def test_session_passes_history_to_the_model(monkeypatch):
    patch_tools(monkeypatch)
    _, model = collect(history=[("who is Arjun?", "Arjun Cohen")], replies=[call("query_identity", question="Arjun Cohen"), final("ok")])
    first = model.seen[0]
    assert [m.content for m in first[1:3]] == ["who is Arjun?", "Arjun Cohen"]


def test_a_failure_becomes_an_error_event_not_an_exception(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def broken(target=None):
        raise ConnectionError("no server")
        yield
    monkeypatch.setattr(session, "open_tools", broken)
    events, _ = collect(replies=[final("x")])
    assert events[-1]["type"] == "error" and "no server" in events[-1]["message"]
