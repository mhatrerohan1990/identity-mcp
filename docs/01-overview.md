# 1. Overview

## What it is

A small rebuild of a production access-governance assistant. A person types a question in English. The system finds similar solved
questions, asks a local language model to write one SQL query, checks that query hard, runs it on a read-only database, and
answers in plain English. The same machinery is offered three ways: a chat page, an MCP server for other tools, and a
command-line agent.

**Core idea:** the model never touches data directly. It only *proposes* SQL. Everything it proposes is checked by plain code
before it runs, and the database connection cannot write even if the checks were wrong.

## The three ways to use it

```
 1. Chat page (browser)        POST /api/agent   (default)  -> agent chains the two tools, streams its steps
                               POST /api/ask                -> single query, no agent
 2. MCP server                 tools query_identity, query_activity  (Claude Desktop, Claude Code, any MCP client)
 3. Command line               python -m identity_mcp.agent "..."     (agent)
                               python -m identity_mcp.pipeline "..."  (single query)
```

All three end in the same place: `pipeline.ask()`, the function that turns one question into a checked query and its rows.

## The layers

Code is organised in layers. A layer may import only from layers below it; `tests/test_architecture.py` fails the build if that is
broken. This keeps each piece testable alone.

```
 interfaces   api/ (web)    mcp_server.py (MCP)    feedback/ (review CLI)    agent/ (our MCP client, talks MCP only)
                     \            |                    /
 pipeline/   engine.py: ask()  = rewrite -> retrieve -> write SQL -> check/repair -> run -> answer
                     |
 rag/          guardrails/          context/
 bank, retrieval   validation, scopes,   conversation (follow-ups),
                   grounding             handoff (ids between tools)
                     |
 core/       db (read-only SQLite), llm (Ollama client), logs
 data/       schema.sql, seed.py (the demo database)
```

| Layer | Package | Job |
|-------|---------|-----|
| core | `core/` | read-only database connections, the Ollama client, logging. Imports nothing else of ours. |
| data | `data/` | the schema and a deterministic seed script |
| rag | `rag/` | load the question bank, embed it, find the closest questions |
| guardrails | `guardrails/` | decide what may run: validation, scopes, value grounding |
| context | `context/` | what the pipeline knows beyond the question: earlier turns, earlier results |
| pipeline | `pipeline/` | the flow for one question |
| interfaces | `api/`, `mcp_server.py`, `feedback/` | the doors into the pipeline |
| agent | `agent/` | an MCP *client*: it only talks to the server over MCP |

## The data it answers questions about

Seven tables of an invented company ("acme.example"): `users`, `groups`, `group_members`, `applications`, `entitlements`,
`access_grants`, `activity_events`. 500 users, about 20,000 events. "Today" is fixed at **2026-11-01** so answers do not change
with the calendar. Three findings are planted on purpose (orphaned access, dormant admins, privileged users without MFA), so tests
and evals know the right answer. Details: [components/data.md](components/data.md).

## Why two tools, not one

In production the identity data and the activity data live in different stores, so no single query can join them. The same
shape is kept here: `query_identity` can read people, groups, apps and grants; `query_activity` can read events (with user and
app names). A question that needs both, such as **dormant admins**, takes two calls whose result is handed from one to the next. The
program that chains them is the *client*: our LangGraph agent. See [components/agent.md](components/agent.md).

## Technology

Python 3.12, `uv` for dependencies, FastAPI (web), the `mcp` SDK 2.x (MCP server and client), SQLite, `sqlglot` (reads SQL as a
tree), Ollama (local models: `qwen2.5-coder:14b` writes SQL and drives the agent, `nomic-embed-text` makes embeddings),
LangGraph (agent loop), pytest. No authentication yet.

## Repository map

```
src/identity_mcp/   the package (layers above)
examples/           examples.md - the question bank (the model's few-shot examples)
evals/              eval suite: cases.json, runner, grading, baselines
tests/              mirrors the package; test_architecture.py enforces the layer rules
docs/               this knowledge base (DESIGN.md is the original, now historical, plan)
```
