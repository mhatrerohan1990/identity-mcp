"""The feedback loop: storing thumbs, checking steps, writing the bank and eval files, and the review command."""

import json
import shutil
from pathlib import Path

import pytest

from identity_mcp.data import seed
from identity_mcp.feedback import __main__ as review_cli
from identity_mcp.feedback import promote, store
from identity_mcp.rag import bank

REPO = Path(__file__).resolve().parents[2]
GOOD = "SELECT COUNT(*) AS n FROM users WHERE status = 'active'"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A seeded database, private copies of the bank and eval cases, and an empty feedback file."""
    seed.build(tmp_path / "app.db")
    shutil.copy(REPO / "examples" / "examples.md", tmp_path / "examples.md")
    shutil.copy(REPO / "evals" / "cases.json", tmp_path / "cases.json")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("IDENTITY_MCP_BANK", str(tmp_path / "examples.md"))
    monkeypatch.setenv("IDENTITY_MCP_FEEDBACK_DB", str(tmp_path / "feedback.db"))
    monkeypatch.setattr(promote, "CASES_PATH", tmp_path / "cases.json")
    con = store.connect()
    yield tmp_path, con
    con.close()


def step(**kw):
    base = dict(tool="query_identity", question="How many people are enabled right now in the directory?", sql=GOOD)
    return store.Step(**{**base, **kw})


def test_feedback_round_trips_with_its_steps(env):
    _, con = env
    fid = store.add(con, rating=store.UP, question="q", answer="a", comment="nice", mode="agent", verified=True, steps=[step()])
    [item] = store.pending(con)
    assert (item.id, item.rating, item.verified, item.steps[0].sql) == (fid, store.UP, True, GOOD)
    store.mark(con, fid, "promoted")
    assert store.pending(con) == [] and store.counts(con) == {"total": 1, "up": 1, "down": 0, "new": 0}


def test_a_good_step_qualifies_and_is_written_in_the_bank_format(env):
    tmp, _ = env
    assert promote.check_step(step()).ok
    entry_id = promote.add_to_bank(step())
    entries = bank.load(tmp / "examples.md")
    assert entry_id == "ex-105" and entries[-1].id == "ex-105" and entries[-1].sql == GOOD and "feedback" in entries[-1].tags
    assert len(entries) == 105  # the new entry parses, and nothing else was lost


@pytest.mark.parametrize("bad, why", [
    (dict(linked=True), "earlier step"),
    (dict(error="boom"), "error"),
    (dict(sql="DELETE FROM users"), "validation"),
    (dict(sql="SELECT * FROM nonexistent_table"), "validation"),
    (dict(sql=""), "no SQL"),
    (dict(question="How many users are there?"), "already has"),  # the same question as a bank entry
])
def test_steps_that_must_not_enter_the_bank(env, bad, why):
    check = promote.check_step(step(**bad))
    assert not check.ok and why in " ".join(check.reasons)
    with pytest.raises(ValueError):
        promote.add_to_bank(step(**bad))


def test_the_scope_is_enforced_for_tool_steps(env):
    check = promote.check_step(step(tool="query_activity", sql="SELECT name FROM groups"))
    assert not check.ok  # activity cannot see groups


def test_an_eval_case_needs_working_sql_and_a_new_question(env):
    tmp, _ = env
    before = (tmp / "cases.json").read_text()
    with pytest.raises(ValueError):
        promote.add_eval_case("How many ghosts?", "SELECT * FROM ghosts")
    assert (tmp / "cases.json").read_text() == before  # nothing written on failure
    assert promote.add_eval_case("How many enabled people are there?", GOOD) == "f-01"
    assert promote.add_eval_case("Another count of enabled people?", GOOD) == "f-02"
    cases = json.loads((tmp / "cases.json").read_text())
    assert cases[-1]["kind"] == "novel" and cases[-1]["sql"] == GOOD
    with pytest.raises(ValueError):
        promote.add_eval_case("how many enabled people are there", GOOD)  # same question, different case


def scripted(*answers):
    queue = list(answers)
    return lambda prompt: queue.pop(0), []


def test_review_adds_a_thumbs_up_step_to_the_bank_when_asked(env):
    tmp, con = env
    store.add(con, rating=store.UP, question="q", answer="a", steps=[step()])
    ask, _ = scripted("a")
    tally = review_cli.review(con, ask, lambda text: None)
    assert tally["promoted"] == 1 and bank.load(tmp / "examples.md")[-1].sql == GOOD
    assert store.pending(con) == []


def test_review_leaves_things_alone_when_you_skip_or_dismiss(env):
    tmp, con = env
    store.add(con, rating=store.UP, question="q1", answer="a", steps=[step()])
    store.add(con, rating=store.UP, question="q2", answer="a", steps=[step()])
    ask, _ = scripted("s", "d")
    tally = review_cli.review(con, ask, lambda text: None)
    assert tally == {"promoted": 0, "dismissed": 1, "skip": 1}
    assert len(bank.load(tmp / "examples.md")) == 104
    assert len(store.pending(con)) == 1  # the skipped one is still waiting


def test_review_turns_a_thumbs_down_into_an_eval_case_with_the_given_sql(env):
    tmp, con = env
    store.add(con, rating=store.DOWN, question="How many enabled people?", answer="wrong", comment="off by a lot", steps=[step(sql="SELECT 1")])
    ask, _ = scripted("g", "SELECT COUNT(*) AS n", "FROM users", "WHERE status = 'active'", "", "")
    shown = []
    tally = review_cli.review(con, ask, shown.append)
    cases = json.loads((tmp / "cases.json").read_text())
    assert tally["promoted"] == 1 and cases[-1]["id"] == "f-01" and cases[-1]["question"] == "How many enabled people?"
    assert "off by a lot" in "\n".join(shown)


def test_a_bad_gold_sql_is_reported_and_asked_again(env):
    _, con = env
    store.add(con, rating=store.DOWN, question="q", answer="a", steps=[step()])
    ask, _ = scripted("g", "SELECT * FROM nope", "", "", "q")
    shown = []
    tally = review_cli.review(con, ask, shown.append)
    assert tally == {"promoted": 0, "dismissed": 0, "skip": 0} and any("not added" in line for line in shown)
