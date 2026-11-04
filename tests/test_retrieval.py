import asyncio
import json

import pytest

from identity_mcp import bank, llm, pipeline, retrieval

run = asyncio.run


def test_loader_parses_the_whole_bank():
    examples = bank.load()
    assert len(examples) == 104 and examples[0].id == "ex-001"
    assert any(e.notes for e in examples) and all(e.sql and e.question for e in examples)


def test_retrieves_the_relevant_entries():
    hits = run(retrieval.retrieve("Which terminated users still have access to applications?", k=3, min_score=0.0))
    assert len(hits) == 3 and hits[0].score >= hits[1].score >= hits[2].score
    assert "ex-062" in {h.example.id for h in hits}  # the orphaned-access entry


def test_k_zero_and_min_score_and_exclude():
    q = "Which terminated users still have access?"
    assert run(retrieval.retrieve(q, k=0)) == []
    assert run(retrieval.retrieve("zzzz qqqq", k=5, min_score=0.9)) == []
    top = run(retrieval.retrieve(q, k=1, min_score=0.0))[0].example.id
    held_out = run(retrieval.retrieve(q, k=5, min_score=0.0, exclude={top}))
    assert top not in {h.example.id for h in held_out}


def test_embeddings_are_cached_on_disk(monkeypatch):
    calls = []
    real = llm.embed

    async def counting(texts, *, timeout=60):
        calls.append(len(texts))
        return await real(texts, timeout=timeout)

    monkeypatch.setattr(llm, "embed", counting)
    run(retrieval.retrieve("how many users", k=1))
    assert calls == [104, 1]  # 104 bank questions embedded once, plus the query
    monkeypatch.setattr(retrieval, "_index", None)  # a fresh process: only the disk cache remains
    calls.clear()
    run(retrieval.retrieve("how many groups", k=1))
    assert calls == [1]
    cache = json.loads(retrieval._cache_file().read_text())
    assert len(cache) == 104


def test_prompt_includes_examples_best_last_with_notes():
    hits = run(retrieval.retrieve("Which terminated users still have access?", k=3, min_score=0.0))
    system = pipeline.build_sql_system(hits)
    assert "CREATE TABLE users" in system and "Worked examples" in system
    assert system.rfind(hits[0].example.sql) > system.rfind(hits[-1].example.sql)  # closest example last
    assert pipeline.build_sql_system([]) == pipeline.SQL_SYSTEM_BASE  # no hits -> unchanged baseline prompt


def test_ask_reports_examples_and_sends_them_to_the_llm(tmp_path, monkeypatch):
    from identity_mcp.data import seed
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))
    seen = {}

    async def chat(messages, *, schema=None, timeout=120):
        if schema:
            seen["system"] = messages[0]["content"]
            return '{"sql": "SELECT COUNT(*) FROM users"}'
        return "ok"

    monkeypatch.setattr(llm, "chat", chat)
    out = run(pipeline.ask("Which terminated users still have access?", k=3))
    assert len(out["examples"]) == 3 and out["examples"][0]["id"].startswith("ex-") and "retrieve" in out["timings_ms"]
    assert "Worked examples" in seen["system"]
    base = run(pipeline.ask("Which terminated users still have access?", k=0))
    assert base["examples"] == [] and seen["system"] == pipeline.SQL_SYSTEM_BASE


def test_ask_excludes_held_out_ids(tmp_path, monkeypatch):
    from identity_mcp.data import seed
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))

    async def chat(messages, *, schema=None, timeout=120):
        return '{"sql": "SELECT 1"}' if schema else "ok"

    monkeypatch.setattr(llm, "chat", chat)
    q = "Which terminated users still have access?"
    first = run(pipeline.ask(q, k=1))["examples"][0]["id"]
    again = run(pipeline.ask(q, k=3, exclude=frozenset({first})))["examples"]
    assert first not in {e["id"] for e in again}


def test_retrieval_failure_degrades_to_no_examples(tmp_path, monkeypatch):
    from identity_mcp.data import seed
    seed.build(tmp_path / "app.db")
    monkeypatch.setenv("IDENTITY_MCP_DB", str(tmp_path / "app.db"))

    async def broken(*a, **k):
        raise llm.LLMError("Model 'nomic-embed-text' not found. Run: ollama pull nomic-embed-text")

    async def chat(messages, *, schema=None, timeout=120):
        return '{"sql": "SELECT 1"}' if schema else "fine"

    monkeypatch.setattr(llm, "embed", broken)
    monkeypatch.setattr(llm, "chat", chat)
    out = run(pipeline.ask("anything"))
    assert out["answer"] == "fine" and out["examples"] == [] and "ollama pull" in out["warning"]
