"""Rescuing tool calls that a model wrote as text; ordinary text must never be touched."""

import json

import pytest
from langchain_core.messages import AIMessage

from identity_mcp.agent.recovery import recover_tool_calls

NAMES = {"query_identity", "query_activity", "final_answer"}
CALL = {"name": "query_identity", "arguments": {"question": "admins"}}


def recovered(text):
    return recover_tool_calls(AIMessage(text), NAMES)


@pytest.mark.parametrize("text", [
    json.dumps(CALL),
    "```json\n" + json.dumps(CALL) + "\n```",
    "```\n" + json.dumps(CALL) + "\n```",
    "<tool_call>\n" + json.dumps(CALL) + "\n</tool_call>",
    json.dumps({"name": "query_identity", "parameters": {"question": "admins"}}),   # "parameters" instead of "arguments"
    json.dumps({"name": "query_identity", "arguments": json.dumps({"question": "admins"})}),  # arguments as a JSON string
    "  " + json.dumps(CALL) + "  \n",
])
def test_text_form_calls_become_real_calls(text):
    reply = recovered(text)
    assert reply.content == "" and [(c["name"], c["args"]) for c in reply.tool_calls] == [("query_identity", {"question": "admins"})]
    assert reply.tool_calls[0]["id"] and reply.tool_calls[0]["type"] == "tool_call"


def test_several_calls_in_a_list():
    reply = recovered(json.dumps([CALL, {"name": "query_activity", "arguments": {"question": "logins"}}]))
    assert [c["name"] for c in reply.tool_calls] == ["query_identity", "query_activity"]


def test_final_answer_is_recognised_too():
    reply = recovered(json.dumps({"name": "final_answer", "arguments": {"answer": "done"}}))
    assert reply.tool_calls[0]["args"] == {"answer": "done"}


@pytest.mark.parametrize("text", [
    "There are 72 admins.",
    "",
    "   ",
    json.dumps({"name": "delete_everything", "arguments": {}}),                       # not a tool we offered
    json.dumps([CALL, {"name": "delete_everything", "arguments": {}}]),               # one bad call spoils the list
    json.dumps({"answer": 42}),                                                      # JSON, but not a call
    json.dumps({"name": "query_identity", "arguments": ["not", "a", "dict"]}),
    json.dumps({"name": "query_identity", "arguments": "not json"}),
    '{"name": "query_identity", "arguments": {"question": "admins"}',                 # broken JSON
    'Sure! ' + json.dumps(CALL),                                                      # prose around the JSON
])
def test_anything_else_is_left_as_the_model_wrote_it(text):
    message = AIMessage(text)
    assert recover_tool_calls(message, NAMES) is message


def test_a_reply_that_already_has_tool_calls_is_untouched():
    message = AIMessage(json.dumps(CALL), tool_calls=[{"name": "query_activity", "args": {}, "id": "x", "type": "tool_call"}])
    assert recover_tool_calls(message, NAMES) is message
