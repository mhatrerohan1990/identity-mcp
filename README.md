# GrantLens

Ask questions about identity & access data in plain English. GrantLens retrieves a few similar
curated question→SQL examples (few-shot RAG), has Claude write a query, validates it with
guardrails, runs it read-only on SQLite, and answers in natural language. Exposed as an MCP server
behind a FastAPI app that also serves a chat UI. A small-scale version of a production governed-query assistant.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

## Quick start

```sh
uv sync
uv run python -m grantlens.data.seed   # writes app.db (deterministic demo data)
uv run pytest
```

## Demo data

500 users, 24 apps, 59 entitlements, ~4.4k grants, ~20k activity events, with planted findings:
30 terminated users who still hold access (orphaned), 15 active admins with no activity in 90+
days (dormant), 20 active members of privileged groups without MFA. "Today" is fixed at
2026-11-01 so results never drift.
