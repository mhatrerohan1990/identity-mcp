"""The agent loop, driven by a scripted model and fake tools: no Ollama, no server."""

import asyncio
import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import Field

from identity_mcp.agent import graph as agent_graph
from identity_mcp.agent import prompts

run = asyncio.run


class ScriptedModel(BaseChatModel):
    """A chat model that replies from a list, one reply per call (repeating the last), and records what it was shown."""

    replies: list[Any]
    seen: list[Any] = Field(default_factory=list)
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        reply = self.replies[min(self.index, len(self.replies) - 1)]
        self.index += 1
        # a real model returns a new message with new tool-call ids every time; reusing one object would be merged as a repeat
        reply = reply.model_copy(update={
            "id": None,
            "tool_calls": [{**c, "id": f"{c['id']}_{self.index}"} for c in reply.tool_calls],
        })
        return ChatResult(generations=[ChatGeneration(message=reply)])


def call(name: str, **args) -> AIMessage:
    """A model reply that asks for one tool call."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}", "type": "tool_call"}])


def fake_tools(log: list, fail: set[str] = frozenset()):
    """The two server tools, faked: each records its arguments and returns a small JSON result."""
    def make(name: str, scope: str):
        async def run_tool(**kwargs):
            log.append((name, kwargs))
            if name in fail:
                raise ValueError("the tool blew up")
            return json.dumps({"scope": scope, "row_count": 3, "columns": ["id"], "rows": [[1], [2], [3]], "result_id": f"r_{name}"})
        return StructuredTool(name=name, description=name, args_schema={"type": "object", "properties": {
            "question": {"type": "string"}, "filter_from": {"type": "object"}}, "required": ["question"]}, coroutine=run_tool)
    return [make("query_identity", "identity"), make("query_activity", "activity")]


def ask(model, tools, question="show me dormant admins", max_steps=6, updates=None):
    graph = agent_graph.build_graph(model, tools, max_steps)
    on_update = (lambda node, update: updates.append(node)) if updates is not None else None
    return run(agent_graph.run(graph, question, on_update))


def final(text: str) -> AIMessage:
    """A model reply that finishes by calling final_answer."""
    return call("final_answer", answer=text)


def test_two_tools_chained_then_an_answer():
    log, updates = [], []
    model = ScriptedModel(replies=[
        call("query_identity", question="active users with admin access"),
        call("query_activity", question="which of these had no activity?", filter_from={"result_id": "r_query_identity", "column": "id"}),
        final("Three admins are dormant."),
    ])
    answer = ask(model, fake_tools(log), updates=updates)
    assert answer == "Three admins are dormant."
    assert [name for name, _ in log] == ["query_identity", "query_activity"]
    assert log[1][1]["filter_from"] == {"result_id": "r_query_identity", "column": "id"}  # the first result's id reached the second call
    assert updates == ["agent", "tools", "agent", "tools", "agent", "finish"]
    assert [type(m).__name__ for m in model.seen[-1]].count("ToolMessage") == 2  # the model saw both results before finishing


def test_the_model_is_shown_the_system_prompt_and_the_question_first():
    model = ScriptedModel(replies=[call("query_identity", question="q"), final("done")])
    ask(model, fake_tools([]), question="who is an admin?")
    first = model.seen[0]
    assert first[0].content == prompts.SYSTEM and isinstance(first[1], HumanMessage) and first[1].content == "who is an admin?"


def test_the_step_budget_stops_a_loop():
    log = []
    model = ScriptedModel(replies=[call("query_identity", question="again")])  # never stops asking
    answer = ask(model, fake_tools(log), max_steps=3)
    assert "ran out of steps (3)" in answer and "query_identity" in answer
    assert len(log) == 2  # steps 1 and 2 ran their tools; step 3 wanted another and was refused


def test_answering_without_any_tool_gets_nudged():
    log = []
    model = ScriptedModel(replies=[AIMessage("I think there are 40 admins."), call("query_identity", question="admins"), final("72 admins.")])
    answer = ask(model, fake_tools(log))
    assert answer == "72 admins." and [n for n, _ in log] == ["query_identity"]
    nudge = model.seen[1][-1]
    assert isinstance(nudge, HumanMessage) and nudge.content == prompts.NUDGE_NO_TOOL


def test_a_final_answer_with_no_data_behind_it_is_sent_back():
    log = []
    model = ScriptedModel(replies=[final("from memory"), call("query_identity", question="admins"), final("72 admins.")])
    assert ask(model, fake_tools(log)) == "72 admins." and [n for n, _ in log] == ["query_identity"]
    assert model.seen[1][-1].content == prompts.NUDGE_NO_TOOL


def test_announcing_the_next_step_in_text_does_not_end_the_run():
    """What llama3.1 did on the first real run: fetched the admins, then wrote 'Now let's check activity' and stopped."""
    log = []
    model = ScriptedModel(replies=[
        call("query_identity", question="admins"),
        AIMessage("Now, let's find out which of these users had no activity in the last 90 days."),
        call("query_activity", question="no activity?", filter_from={"result_id": "r_query_identity", "column": "id"}),
        final("Two are dormant."),
    ])
    assert ask(model, fake_tools(log)) == "Two are dormant."
    assert [n for n, _ in log] == ["query_identity", "query_activity"]
    assert model.seen[2][-1].content == prompts.NUDGE_PLAIN_TEXT  # told to act, not narrate


def test_nudges_are_limited_and_then_the_text_is_accepted():
    model = ScriptedModel(replies=[AIMessage("from memory")])  # keeps answering without a tool
    log = []
    assert ask(model, fake_tools(log)) == "from memory" and log == []
    assert model.index == graph_limit() + 1  # the first reply plus one reply per nudge


def graph_limit() -> int:
    return agent_graph.MAX_NUDGES


def test_a_tool_call_written_as_text_is_recovered_and_run():
    """What qwen2.5-coder does: the call arrives as JSON in the message body."""
    log = []
    as_text = AIMessage(json.dumps({"name": "query_identity", "arguments": {"question": "admins"}}))
    model = ScriptedModel(replies=[as_text, final("72 admins.")])
    assert ask(model, fake_tools(log)) == "72 admins." and log == [("query_identity", {"question": "admins"})]


def test_a_failing_tool_becomes_a_message_the_model_can_react_to():
    log = []
    model = ScriptedModel(replies=[call("query_identity", question="x"), call("query_activity", question="y"), final("recovered")])
    answer = ask(model, fake_tools(log, fail={"query_identity"}))
    assert answer == "recovered" and [n for n, _ in log] == ["query_identity", "query_activity"]  # the run went on
    tool_messages = [m for m in model.seen[1] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1 and "blew up" in tool_messages[0].content


def test_default_budget_allows_a_two_tool_chain_with_room_to_spare():
    assert agent_graph.DEFAULT_MAX_STEPS >= 4  # two tool calls plus final_answer needs 3; one retry needs 4
