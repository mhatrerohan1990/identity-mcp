"""Multi-turn: follow-ups are rewritten into standalone questions before the normal pipeline runs."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from identity_mcp.context import conversation
from identity_mcp.core import llm
from identity_mcp import pipeline
from identity_mcp.api.app import app
from identity_mcp.context.conversation import Turn
from identity_mcp.data import seed

run = asyncio.run

COHENS = Turn(
    question="More info on Arjun Cohen",
    answer="There are two people named Arjun Cohen.",
    columns=["id", "name", "email", "created_at"],
    rows=[[405, "Arjun Cohen", "arjun.cohen@acme.example", "2019-06-13"],
          [436, "Arjun Cohen", "arjun.cohen36@acme.example", "2025-10-03"]],
)
STANDALONE = "Show more info on the user with id 436 (Arjun Cohen, arjun.cohen36@acme.example)"


def scripted(rewrite_reply, sql="SELECT COUNT(*) FROM groups", calls=None):
    """Fake model: the rewrite call (recognised by its schema) gets `rewrite_reply`, SQL calls get `sql`, the rest an answer."""
    async def chat(messages, *, schema=None, timeout=120):
        if calls is not None:
            calls.append((schema, messages))
        if schema is conversation.REWRITE_SCHEMA:
            if isinstance(rewrite_reply, Exception):
                raise rewrite_reply
            return rewrite_reply
        return json.dumps({"sql": sql}) if schema else "answer"
    return chat


def reply(text):
    return json.dumps({"question": text})


# --- the rewrite step ------------------------------------------------------------------------------------

def test_no_history_means_no_model_call(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted(reply("never used"), calls=calls))
    for empty in (None, []):
        result = run(conversation.standalone_question("How many users?", empty))
        assert result == conversation.Rewrite("How many users?") and not result.changed
    assert calls == []


def test_follow_up_is_rewritten_using_the_previous_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted(reply(STANDALONE), calls=calls))
    result = run(conversation.standalone_question("the newer one", [COHENS]))
    assert result.question == STANDALONE and result.changed and result.warning is None
    (schema, messages), = calls
    prompt = messages[1]["content"]
    assert "436 | Arjun Cohen | arjun.cohen36@acme.example" in prompt  # the ids are what make "the newer one" resolvable
    assert prompt.endswith("Latest message: the newer one")


def test_a_message_that_is_already_complete_comes_back_unchanged(monkeypatch):
    monkeypatch.setattr(llm, "chat", scripted(reply("How many groups are there?")))
    result = run(conversation.standalone_question("How many groups are there?", [COHENS]))
    assert result.question == "How many groups are there?" and not result.changed


@pytest.mark.parametrize("bad", [
    llm.LLMError("ollama is down"), "not json", json.dumps({"other": 1}), reply(""), reply("x" * 700),
])
def test_a_failed_rewrite_falls_back_to_the_question_as_typed(monkeypatch, bad):
    monkeypatch.setattr(llm, "chat", scripted(bad))
    result = run(conversation.standalone_question("the newer one", [COHENS]))
    assert result.question == "the newer one" and not result.changed and "as typed" in result.warning


def test_describe_keeps_the_last_turns_a_few_rows_and_short_cells():
    turns = [Turn(question=f"question {i}") for i in range(5)]
    turns[-1] = Turn(question="last", answer="a" * 500, columns=["v"], rows=[["x" * 200]] + [[i] for i in range(10)])
    text = conversation.describe(turns)
    assert "question 0" not in text and "question 1" not in text and "question 2" in text  # last 3 only
    assert text.count("Turn ") == 3
    assert "x" * 100 not in text and "…" in text                    # long cells are cut
    assert "a" * 301 not in text                                     # long answers are cut
    assert "6 more row(s) not shown" in text                         # 11 rows, 5 shown


def test_turn_rejects_oversized_input():
    with pytest.raises(ValidationError):
        Turn(question="q" * 1001)
    with pytest.raises(ValidationError):
        Turn(question="q", rows=[[1]] * 21)
    with pytest.raises(ValidationError):
        Turn(question="q", columns=["c"] * 51)


# --- inside the pipeline ---------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))


def test_pipeline_runs_on_the_rewritten_question(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted(reply(STANDALONE), calls=calls))
    out = run(pipeline.ask("the newer one", history=[COHENS], k=0))
    assert out["question"] == "the newer one"                     # what the user typed is preserved
    assert out["standalone_question"] == STANDALONE and "rewrite" in out["timings_ms"]
    sql_call = next(m for s, m in calls if s is pipeline.SQL_SCHEMA)
    assert sql_call[-1]["content"] == STANDALONE                  # SQL is generated from the rewritten question
    summary_call = next(m for s, m in calls if s is None)
    assert STANDALONE in summary_call[-1]["content"]              # and the answer is written for it too
    assert "error" not in out and "warning" not in out


def test_first_question_makes_no_extra_model_call(env, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "chat", scripted(reply("unused"), calls=calls))
    out = run(pipeline.ask("how many groups?", k=0))
    assert [s is conversation.REWRITE_SCHEMA for s, _ in calls] == [False, False]  # SQL + summary only
    assert "standalone_question" not in out and "rewrite" not in out["timings_ms"]


def test_failed_rewrite_still_answers_and_says_so(env, monkeypatch):
    monkeypatch.setattr(llm, "chat", scripted(llm.LLMError("boom")))
    out = run(pipeline.ask("how many groups?", history=[COHENS], k=0))
    assert out["answer"] == "answer" and "as typed" in out["warning"] and "standalone_question" not in out


def test_unchanged_follow_up_is_not_reported_as_rewritten(env, monkeypatch):
    monkeypatch.setattr(llm, "chat", scripted(reply("how many groups?")))
    out = run(pipeline.ask("how many groups?", history=[COHENS], k=0))
    assert "standalone_question" not in out


# --- the HTTP entry point (MCP clients keep their own conversation; see agent.py) ------------------------

def test_api_accepts_history_and_limits_it(env, monkeypatch):
    seen = {}

    async def fake_ask(question, *, history=None, **kw):
        seen["history"] = history
        return {"question": question, "answer": "ok"}

    monkeypatch.setattr(pipeline, "ask", fake_ask)
    client = TestClient(app)
    body = {"question": "the newer one", "history": [COHENS.model_dump()]}
    assert client.post("/api/ask", json=body).status_code == 200
    assert seen["history"] == [COHENS]
    assert client.post("/api/ask", json={"question": "x"}).status_code == 200 and seen["history"] == []
    too_long = {"question": "x", "history": [COHENS.model_dump()] * 7}
    assert client.post("/api/ask", json=too_long).status_code == 422
