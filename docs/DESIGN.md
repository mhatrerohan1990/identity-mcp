# GrantLens design

## Goal
Plain-English question in, plain-English answer out, with the SQL, result rows, and the examples
that guided it. One core pipeline, served over MCP so a chat UI, Claude Desktop, or Claude Code
can all use it.

Pattern: **retrieve few-shot examples → Claude writes SQL → guardrails → execute → summarize**.
The retrieval is over curated *examples* (question→SQL pairs), not over schema. The schema DDL is
fixed context in the prompt.

## Architecture
```
Browser (static HTML/JS) ──REST──▶ FastAPI app ◀──MCP (streamable HTTP at /mcp)── Claude Desktop / Code
                                    │   (stdio entrypoint also provided)
                                    │   REST: /api/ask, /api/feedback, /api/admin/*
                                    │   MCP tools: ask / search_examples / get_schema / run_sql
                                    ▼
                        core pipeline (plain Python, no web or MCP dependency)
                          1. retrieve top-k examples (examples.md → embeddings + BM25)
                          2. prompt = schema DDL + k examples + question
                          3. Claude → {sql, reasoning, tables_used}
                          4. guardrails (sqlglot); on failure repair, max 2 attempts
                          5. execute on read-only SQLite (timeout, row cap)
                          6. Claude → natural-language answer from rows
                                  │
                       app.db (read-only)    meta.db (feedback, query log)
```

## Web layer
One FastAPI app serves the chat page, the REST API, and the MCP server (FastMCP's streamable-HTTP
app mounted at `/mcp`). REST routes and MCP tools both call the same core `ask()`, so the two front
doors cannot drift. The browser uses REST rather than an MCP client: simpler, and MCP stays the
integration surface for Claude Desktop and Claude Code. The UI is a single static page, no build step.

## Orchestration
No LangGraph. The pipeline is a fixed sequence with one bounded repair loop, which a plain
function handles. The MCP server only exposes tools; the client decides when to call them. Revisit
if we need durable checkpointed state, human approval before execution, or branching agent flows.

## Components
- **Dataset**: `src/grantlens/data/schema.sql` and `seed.py`. Deterministic; planted findings
  (orphaned access, dormant admins, privileged users without MFA) are asserted in `tests/test_seed.py`.
- **Example bank**: `examples/examples.md`, ~100-150 curated pairs (id, question, tags, SQL, notes).
  Source of truth; embeddings are cached keyed by file hash.
- **Retrieval**: local `fastembed` embeddings + cosine in numpy, fused with BM25 (domain words like
  "MFA", "orphaned" matter). Top 5, drop below a minimum score.
- **Guardrails**: sqlglot parse; single `SELECT`/`WITH…SELECT` only; table/column allowlist from the
  schema (catches hallucinated columns); no `ATTACH`/`PRAGMA`; inject `LIMIT 200`; read-only
  connection (`mode=ro`, `query_only`) with ~2s timeout. Failures feed back to Claude (max 2 repairs),
  then an honest "couldn't answer".
- **Answer step**: Claude summarizes from rows only; must not state numbers absent from the rows.
- **MCP tools**: `ask(question)`, `search_examples(question, k)`, `get_schema()`, `run_sql(sql)`.
- **UI**: static chat page served by FastAPI; per answer: text, collapsible SQL, result table, retrieved examples with
  scores, latency, 👍/👎.
- **Feedback loop**: votes to `meta.db`; admin view lists 👍 pairs not yet in the bank with a
  "promote to examples.md" action.
- **Evals**: `evals/golden.jsonl` (~30 held-out questions). Score by comparing result sets, not SQL
  text. Report execution accuracy, guardrail rejection rate, p50/p95 latency, and k=0 vs k=5.

## Build order
1. ✅ Schema, seed data (+ ~40 examples next)
2. Core pipeline as a CLI
3. Guardrails and repair loop, with unit tests
4. FastAPI app with the MCP server mounted (MCP Inspector, Claude Desktop)
5. REST API and chat page
6. Feedback, promote action, evals

## Out of scope for v1
Multi-turn follow-ups, auth, non-SQLite backends.
