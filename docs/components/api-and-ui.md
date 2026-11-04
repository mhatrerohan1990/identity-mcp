# API and chat page

**Files:** `src/identity_mcp/api/` - `app.py`, `agent_routes.py`, `feedback_routes.py`, `routes.py`, `db.py`, `static/index.html`

## The FastAPI app (`app.py`)

One process serves everything: the chat page, the question endpoints, the REST data endpoints, and the MCP server. At startup it
configures logging and runs the mounted MCP app's lifespan (a mounted app's startup is not run by its parent automatically).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | the chat page (one static HTML file) |
| `/health` | GET | liveness; touches neither the database nor the model |
| `/api/agent` | POST | the agent, streamed as NDJSON (default in the chat page) |
| `/api/ask` | POST | the single-query pipeline, one JSON response |
| `/api/feedback` | POST | store a thumbs up/down |
| `/users`, `/groups`, `/group-members`, `/applications`, `/entitlements`, `/access-grants`, `/activity-events` (+ nested like `/users/{id}/grants`) | GET | read-only REST access to the data, with filters and pagination |
| `/mcp` | MCP | the same two tools, over streamable HTTP |
| `/docs` | GET | interactive OpenAPI documentation for everything above |

The MCP app is mounted last at `/`, so FastAPI's own routes win.

### `POST /api/agent` (`agent_routes.py`)

A request to change data ("delete all admins") is refused first: the stream is one `answer` event with `refused: true` and the read-only message, with no model call.

Body: `{"question": "...", "history": [{"question": "...", "answer": "..."}]}`. Question 1 to 1000 chars; at most 6 history turns
accepted, the last 3 used; extra fields on a turn (rows, columns) are ignored so the page can send one history array to both
endpoints. Response: `application/x-ndjson`, one event per line, ending with `answer` or `error`. See
[../02-request-flow.md](../02-request-flow.md) for the events in order.

### `POST /api/ask`

Body: `{"question": "...", "history": [Turn]}` where a `Turn` has `question`, `answer` and the first rows. Response: one JSON object with
`answer`, `sql`, `columns`, `rows`, `row_count`, `truncated`, `examples` (what retrieval found), `attempts` (including rejected
queries), `repairs`, `standalone_question`, `warning`, `error` and `timings_ms`.

### `POST /api/feedback` (`feedback_routes.py`)

Body: `rating` ("up"/"down"), `question`, `answer`, optional `comment`, `mode`, `verified`, and up to 8 `steps`
(`tool`, `question`, `sql`, `linked`, `error`). Returns `201 {"id": n}`. Field lengths are limited so a buggy client cannot send a
book.

### Read-only REST endpoints (`routes.py`)

Plain, parameterised queries over the same data, independent of the model: no Ollama needed. List endpoints take `limit` (1 to 200,
default 50) and `offset` and return `{items, total, limit, offset}`. Examples:

```sh
curl 'localhost:8000/users?status=terminated&limit=3'
curl 'localhost:8000/users/48/grants'
curl 'localhost:8000/access-grants?is_admin=true&granted_via=group'
curl 'localhost:8000/activity-events?user_id=48&success=false&since=2026-10-01'
```

Each connection is opened per request, read-only (`db.get_db`); a missing database returns 503 with the seed command.

## The chat page (`static/index.html`)

A single file: HTML, CSS and JavaScript, no build step. Model output is inserted with `textContent` only, never parsed as HTML.

What it does:

- **Mode drop-down.** "Agent (chains tools)" calls `/api/agent`; "Single query" calls `/api/ask`.
- **Live trace (agent mode).** Reads the NDJSON stream and draws each step as it arrives (→ tool and question, ← row count), then
  replaces it with the answer on top and the steps folded underneath ("Steps (5)"), each with its SQL and a rows table. It shows
  "checked against the results" or "not fully verified".
- **Single mode details.** Collapsible SQL ("fixed after 1 retry" when repaired), rejected attempts with reasons, result table, the
  interpreted question for follow-ups, and a timing breakdown.
- **Chat memory.** Keeps the finished turns in a JavaScript array and sends the last three with each question. It is lost on
  refresh. "New chat" clears it.
- **Feedback.** 👍/👎 under every answer. 👎 asks "What was wrong?". It collects the steps (tool, question, SQL, linked, error) while
  streaming and posts them to `/api/feedback`.
- **Sample questions** on the welcome screen; Enter sends, Shift+Enter inserts a newline; light and dark themes follow the system.

## Not here yet

No authentication or authorization (anyone who can reach the port can use every endpoint), no saved chats, no rate limits.
