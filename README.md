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
| Question bank: 100 English questions with expected SQL (`examples/examples.md`) | done |
| Retrieval (RAG) of similar bank examples into the SQL prompt | done, modest gain so far (see below) |
| Validation before execution: static checks, then an `EXPLAIN` dry run | done |
| Harness: repair loop, proper eval suite | next |
| Chat UI (static page + `POST /api/ask`) | done, basic |
| Feedback, evals, per-table tools | planned |

## Run it

Prerequisites:
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).
- [Ollama](https://ollama.com) running locally with a model pulled, only needed for the `ask` tool:
  `ollama pull llama3.1:8b` and `ollama pull nomic-embed-text` (the REST API and tests do not need them).

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
| `IDENTITY_MCP_EMBED_MODEL` | `nomic-embed-text` | Embedding model for example retrieval |
| `IDENTITY_MCP_EXAMPLES_K` | `4` | Bank examples added to the SQL prompt; `0` turns retrieval off |
| `IDENTITY_MCP_EXAMPLES_MIN_SCORE` | `0.5` | Minimum cosine similarity for an example to be used |
| `IDENTITY_MCP_BANK` | `examples/examples.md` | Question bank file |
| `IDENTITY_MCP_LOG_LEVEL` | `INFO` | Level for the flow log (`DEBUG`, `INFO`, `WARNING`, ...) |
| `IDENTITY_MCP_LOG_PROMPTS` | `1` | Log the full prompts and raw LLM replies; `0` hides them |
| `IDENTITY_MCP_CACHE_DIR` | `.cache` | Where bank embeddings are cached (re-embedded automatically when a question changes) |

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

## Validation

Generated SQL is validated before it runs; a query that fails is **not executed** and the user is told why.

1. **Static check** (parsed and inspected, never run): exactly one `SELECT`/`WITH...SELECT` (no `DELETE`,
   `PRAGMA`, `ATTACH`, second statement, `load_extension`, table-valued functions); every table is in the
   schema; every column resolves against its table (catches invented columns, a column on the wrong
   table, reused aliases); no `date('now')`/`CURRENT_DATE`, because the data's "today" is fixed at 2026-11-01.
2. **Dry run**: `EXPLAIN` on the read-only connection, so SQLite itself confirms it can compile the query
   (catches things like unknown functions) without executing it.

The schema used for checking is read from `schema.sql`, so there is no second copy. On failure `ask`
returns `validation_errors` (a list of short messages that name the problem and the columns that do
exist), `error`, and an `answer` explaining that nothing was run; the rejected `sql` is still returned.
All 100 question-bank queries pass validation (tested), and 18 bad or hostile queries are rejected at the
right stage.

Validation catches broken and unsafe SQL, not wrong SQL: a valid query that answers the wrong question
(say, an invented date filter) passes. That is what the examples and the eval are for. The next step feeds
these messages back to the model for one retry.

## Logging

Every `ask` is logged to stderr with a request id, so you can follow one question through the whole flow
(from the server, the CLI, or the stdio MCP server; stdout stays clean for the protocol):

```
12:32:01.838 INFO  [d15e10d7] flow   | ask start ... question='Which active admins have had no activity ...'
12:32:01.945 INFO  [d15e10d7] flow   | retrieved 4 example(s) in 106 ms: ex-068 0.909 '...'; ex-067 0.799 '...'
12:32:01.945 DEBUG [d15e10d7] prompt | ===== LLM request model=llama3.1:8b structured=yes =====
                                        --- system --- (rules, schema, retrieved examples with notes)
                                        --- user --- (the question)
12:32:06.036 DEBUG [d15e10d7] prompt | ===== LLM response (4091 ms) ===== {"sql": "..."}
12:32:06.036 INFO  [d15e10d7] flow   | generated sql in 4091 ms: SELECT ...
12:32:06.040 INFO  [d15e10d7] flow   | validation passed (static + dry run) in 4 ms
12:32:06.045 INFO  [d15e10d7] flow   | executed in 9 ms: 15 row(s) columns=[...] first_row=[...]
12:32:08.072 INFO  [d15e10d7] flow   | summarized in 2026 ms: ...
12:32:08.072 INFO  [d15e10d7] flow   | ask done in 6233 ms
```

The `prompt` lines are the exact messages sent to Ollama and its raw replies, and they are large (about 9 KB
per question). Set `IDENTITY_MCP_LOG_PROMPTS=0` to keep only the one-line `flow` steps. Prompts and the
answer-step prompt contain query results, so treat these logs as sensitive once the data is real.

**How retrieval works.** The bank's 100 questions are embedded once (cached in `.cache/`). For each
incoming question, the closest `k` entries are added to the SQL prompt as worked examples (question,
SQL, and notes), best match last. `ask` returns the entries it used in `examples` (id, question,
score). If the embedding model is unavailable it answers without examples and sets `warning`.

**How much it helps (measured, small sample).** 16 reworded questions, each with its own bank entry
hidden, scored by whether the result contains the right entities: 8/16 without examples, 9 to 11/16 with
k=2 to 4. A real gain, but within noise on 16 questions, and `nomic-embed-text` scores cluster between 0.5
and 0.8, so `IDENTITY_MCP_EXAMPLES_MIN_SCORE` cannot yet filter out irrelevant matches. Remaining
failures are mostly invented columns and wrong tables, which the guardrails and repair loop will target.

**Known limits of this basic flow.** There is no repair loop yet: invalid SQL is rejected, not retried. An 8B model also sometimes writes
valid but wrong SQL, and the answer step can phrase things awkwardly and is slow on long result lists.
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
src/identity_mcp/        pipeline.py (ask), validation.py, retrieval.py (RAG), bank.py, llm.py (Ollama client), logs.py, mcp_server.py, db.py
examples/examples.md  100 question -> expected SQL pairs (verified by tests/test_examples.py)
tests/                seed, API, MCP and question-bank tests
docs/DESIGN.md
```
