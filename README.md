# identity-mcp

Ask questions about identity & access data in plain English. identity-mcp retrieves a few similar
curated question→SQL examples (few-shot RAG), has Claude write a query, validates it with
guardrails, runs it read-only on SQLite, and answers in natural language. Exposed as an MCP server
behind a FastAPI app that also serves a chat UI. A small-scale version of a production
governed-query assistant.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

## Status

| Step | State |
|---|---|
| Schema + deterministic seed data | done |
| Simple read-only REST API per entity | done |
| MCP server with one `ask` tool (question → SQL → rows → answer, local Llama) | done, basic flow |
| Harness: example bank + retrieval, SQL guardrails, repair loop | next |
| Chat UI (static page + `POST /api/ask`) | done, basic |
| Feedback, evals, per-table tools | planned |

## Run it

Prerequisites:
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).
- [Ollama](https://ollama.com) running locally with a model pulled, only needed for the `ask` tool:
  `ollama pull llama3.1:8b` (the REST API and tests do not need it).

```sh
# 1. install dependencies into .venv
uv sync

# 2. create the demo database (writes ./app.db; deterministic, safe to re-run)
uv run python -m identity_mcp.data.seed

# 3. run the tests
uv run pytest

# 4. start the API with auto-reload
uv run uvicorn identity_mcp.api.app:app --reload --port 8000
```

If port 8000 is taken, pick another with `--port`. Then open:

- **Chat UI: http://localhost:8000/**. Type a question and press Enter. Each answer has collapsible
  SQL and result-table sections, plus timings. It needs Ollama running (see prerequisites).
- Interactive docs (try every endpoint in the browser): http://localhost:8000/docs
- Health check: http://localhost:8000/health

Configuration (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `IDENTITY_MCP_DB` | `./app.db` | SQLite file. Always opened read-only; if missing, REST returns `503` with the seed command |
| `IDENTITY_MCP_LLM_URL` | `http://localhost:11434` | Ollama server |
| `IDENTITY_MCP_LLM_MODEL` | `llama3.1:8b` | Model used for both SQL generation and answers |

## MCP server

One tool, `ask(question)`. It sends the question and the schema to the LLM, which writes a SQL query;
the query runs read-only (5s limit, 200-row cap); the LLM then turns the rows into a plain-English
answer. It returns `answer`, `sql`, `columns`, `rows`, `row_count`, `truncated`, `timings_ms`, and
`error` if something failed.

The same server is available two ways:

```sh
# HTTP: starts with the API above, endpoint http://localhost:8000/mcp
uv run uvicorn identity_mcp.api.app:app --port 8000

# stdio: for clients that launch the server themselves
uv run python -m identity_mcp.mcp_server
```

Try the pipeline without MCP:

```sh
uv run python -m identity_mcp.pipeline "Which terminated users still have access?"
```

Connect Claude Code (with the HTTP server running):

```sh
claude mcp add --transport http identity-mcp http://localhost:8000/mcp
```

Connect Claude Desktop (stdio): add this to `claude_desktop_config.json`, with your absolute path:

```json
{
  "mcpServers": {
    "identity-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/this/repo", "run", "python", "-m", "identity_mcp.mcp_server"]
    }
  }
}
```

Inspect it interactively: `npx @modelcontextprotocol/inspector`, then connect to `http://localhost:8000/mcp`
(Streamable HTTP).

**Known limits of this basic flow.** There are no retrieved examples and no query validation yet, so
an 8B model sometimes writes plausible but wrong SQL (for example, missing group-inherited access)
and the answer step can phrase things awkwardly. Both are what the next step (the harness) addresses.
The tests use a fake LLM and do not need Ollama.

## API

No authentication or authorization yet. All endpoints are `GET` and read-only.

| Entity | Endpoints | Filters |
|---|---|---|
| users | `/users`, `/users/{id}`, `/users/{id}/grants`, `/users/{id}/groups`, `/users/{id}/activity` | `status`, `department`, `mfa_enabled`, `manager_id`, `q` (name/email substring) |
| groups | `/groups`, `/groups/{id}`, `/groups/{id}/members` | `is_privileged` |
| group memberships | `/group-members` | `group_id`, `user_id` |
| applications | `/applications`, `/applications/{id}`, `/applications/{id}/entitlements` | `criticality`, `integration`, `owner_user_id` |
| entitlements | `/entitlements`, `/entitlements/{id}` | `app_id`, `is_admin` |
| access grants | `/access-grants`, `/access-grants/{id}` | `user_id`, `entitlement_id`, `app_id`, `group_id`, `granted_via`, `is_admin` |
| activity events | `/activity-events`, `/activity-events/{id}` | `user_id`, `app_id`, `event_type`, `success`, `since`, `until` |

Every list endpoint takes `limit` (1-200, default 50) and `offset`, and returns
`{"items": [...], "total": N, "limit": L, "offset": O}`. Grants and memberships come back with
user, app, entitlement and group names joined in. Unknown ids return `404`; invalid filter values
return `422`.

### Examples

```sh
# terminated users (the orphaned-access candidates)
curl 'localhost:8000/users?status=terminated&limit=5'

# everything one user can reach
curl 'localhost:8000/users/31/grants'

# active-looking admin access granted directly, not via a group
curl 'localhost:8000/access-grants?is_admin=true&granted_via=direct'

# privileged groups and who is in them
curl 'localhost:8000/groups?is_privileged=true'
curl 'localhost:8000/groups/9/members'

# failed admin actions in October
curl 'localhost:8000/activity-events?event_type=admin_action&success=false&since=2026-10-01&until=2026-11-01'
```

## Demo data

500 users, 12 groups, 24 apps, 59 entitlements, ~4.4k grants, ~20k activity events, with planted
findings: 30 terminated users who still hold access (orphaned), 15 active admins with no activity
in 90+ days (dormant), 20 active members of privileged groups without MFA. "Today" is fixed at
2026-11-01 so results never drift.

## Layout

```
src/identity_mcp/data/   schema.sql, seed.py
src/identity_mcp/api/    app.py (chat page, /api/ask, mounts MCP), routes.py, db.py, static/index.html
src/identity_mcp/        pipeline.py (ask), llm.py (Ollama client), mcp_server.py, db.py
tests/                seed and API tests
docs/DESIGN.md
```
