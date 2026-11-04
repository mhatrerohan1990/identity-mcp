# MCP server: two tools

**File:** `src/identity_mcp/mcp_server.py`

## What MCP is

The *Model Context Protocol* is a standard for one program (the **client**, usually containing a model) to discover and call
tools offered by another (the **server**). The server publishes a list of tools, each with a name, a description and an input
schema. The client's model reads the descriptions and decides which tool to call and with what arguments. Claude Desktop and Claude
Code are MCP clients; our LangGraph agent is another.

## The tools

| Tool | Reads | Use for |
|------|-------|---------|
| `query_identity(question, filter_from?)` | users, groups, group_members, applications, entitlements, access_grants | who people are, group membership, applications, who holds what access (directly or through a group) |
| `query_activity(question, filter_from?)` | activity_events (+ users, applications for names) | logins, reads, writes, admin actions; last seen; failures; IP addresses |

Each tool takes a **plain-English question** (not SQL) and answers it with the same guarded pipeline, restricted to its own tables.
The descriptions say what each tool cannot do ("Cannot answer anything about activity; use query_activity"), because the description
is the only guidance the client's model gets.

`filter_from` (optional) is `{"result_id": ..., "column": ...}`: restrict the question to ids from an earlier call. See
[context.md](context.md).

## What a call returns

No prose. The caller's model writes the prose; this saves the slowest model call.

```json
{
  "scope": "identity",
  "question": "Which active users have admin access?",
  "sql": "SELECT DISTINCT u.id, u.name, u.email FROM users u JOIN access_grants g ON ...",
  "columns": ["id", "name", "email"],
  "rows": [[169, "Amara Johnson", "amara.johnson13@acme.example"], "... first 25 rows"],
  "row_count": 72,
  "truncated": false,
  "shown": 25,
  "result_id": "r_f0cddb"
}
```

`row_count` is the number of rows the query returned (capped at 200 by the executor); `rows` shows the first 25 (`SHOWN_ROWS`);
`result_id` refers to the full result for 30 minutes. On failure the result has `error` (and possibly `validation_errors` or
`warning`) instead of rows, so the client can react: rephrase, use the other tool, rerun a step.

## How a call is handled (`_run`)

1. If `filter_from` is present, look the values up in `handoff.STORE`; an unknown or expired id returns `{"error": ...}`.
2. `pipeline.ask(question, scope=..., inputs=..., summarize_answer=False)`.
3. Shape the output: first 25 rows, counts, SQL, and store the full result under a new `result_id`.

Because the two tools share one process-level store, the id from `query_identity` is valid in `query_activity`.

## Running it

```sh
uv run python -m identity_mcp.mcp_server     # stdio: for Claude Desktop / Claude Code
```

or over HTTP: the FastAPI app mounts the same server at `http://localhost:8000/mcp` (streamable HTTP). Logs go to stderr because
stdout carries the protocol.

Claude Desktop configuration sketch (`claude_desktop_config.json`):

```json
{ "mcpServers": { "identity-mcp": {
    "command": "uv",
    "args": ["--directory", "/path/to/isopod", "run", "python", "-m", "identity_mcp.mcp_server"] } } }
```

## Design note

In production, identity data and activity data live in different stores (a relational database and a search index), so a question
spanning both is *necessarily* several tool calls with client-side orchestration. The project keeps that shape on purpose: two
scoped tools, a hand-off, and a client (our agent) that chains them. A single `ask` tool over all tables would have been simpler and
would have hidden the interesting problem.

## How it fails

A request to change data ("delete all admins") comes back as `{"refused": true, "error": "This assistant is read-only, ..."}` before any model runs, so a client can relay it.


An invalid or out-of-scope query returns `error` with the reasons (after the pipeline's own repairs). The server itself does not
crash on bad input; arguments are validated by the MCP SDK from the tool's type hints.
