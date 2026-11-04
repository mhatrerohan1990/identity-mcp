# Pipeline: question to SQL to rows to answer

**Files:** `src/identity_mcp/pipeline/` (`engine.py`, `prompts.py`, `sql.py`, `executor.py`, `answer.py`, `__main__.py`),
`core/llm.py`, `core/logs.py`

## What it is

The function `pipeline.ask(question, ...)` is the heart of the system. Everything else (web page, MCP tools, agent) is a way of
calling it. It runs the steps below and never raises for an expected failure: problems come back in the result's `error` and
`answer` fields.

```
ask(question)
  1. rewrite follow-up (only with history)        context/conversation.py   -> model call
  2. retrieve k examples                          rag/retrieval.py          -> embedding call
  3. build the prompt                             pipeline/prompts.py
  4. write SQL                                    pipeline/sql.py           -> model call
  5. check; on failure repair (up to 2x)          pipeline/sql.py + guardrails/   -> model call per repair
  6. execute (sandboxed, 5 s, 200 rows)           pipeline/executor.py
  7. write the plain-English answer (optional)    pipeline/answer.py        -> model call
```

## Arguments that change the behaviour

| Argument | Used by | Effect |
|----------|---------|--------|
| `history` | chat page, single-query mode | earlier turns; a follow-up is rewritten first |
| `scope` | MCP tools | prompt, retrieval, validation and sandbox restricted to one tool's tables |
| `inputs` | `query_activity` with `filter_from` | ids from an earlier call exposed as the table `input` |
| `summarize_answer` | MCP tools pass `False` | skip step 7 (the slowest call; the caller reasons over rows itself) |
| `k`, `exclude` | evals | number of examples (0 = none); hide bank ids to test generalisation |

## The prompt (`prompts.py`)

Plain strings and small functions; no model calls. The system prompt, in order:

1. a one-line role ("You translate questions about an identity and access governance database into one SQLite query");
2. the **schema** (only the scope's tables when scoped);
3. **rules** (common rules, plus identity or activity rules, plus scoped-tool rules, plus a sentence about `input` when inputs exist);
4. the response format: `{"sql": "<the query>"}`;
5. the **retrieved examples**, best match last.

Rules that came from real model mistakes include: *add only the filters the question asks for*; *"how many" returns one number*;
*questions about people return one row per person*; *match a named person with `name LIKE '%Name%'` and include id and email*; *today is
2026-11-01*; *scoped tools return `id` first*.

An excerpt of what the model receives for the MFA question above (shape, abbreviated):

```
You translate questions about an identity and access governance database into one SQLite query.

Database schema:
CREATE TABLE users ( id INTEGER PRIMARY KEY, ... mfa_enabled INTEGER ... );
...
Rules:
- Write a single read-only SELECT statement in SQLite dialect. ...
- Treat today as 2026-11-01. Use date('2026-11-01', '-90 days') ...
Respond with JSON: {"sql": "<the query>"}

Worked examples of similar questions with the correct query. ... (the closest example is last):
Question: Which users with admin access to high-criticality applications have no MFA?
SQL: SELECT ...
...
Question: How many privileged users lack MFA?
SQL: SELECT ...

[user] How many suspended users have MFA disabled?
```

## Writing and repairing SQL (`sql.py`)

- `generate_sql`: one chat call with a JSON schema `{"sql": string}`, so the reply is always parseable. `_strip_fences` removes
  Markdown fences or trailing semicolons if the model adds them.
- `check_sql`: the pre-execution checks in order (see [guardrails.md](guardrails.md)).
- `produce_sql`: generate, check, and on failure **repair**: the conversation so far is replayed (the model's own query as its reply,
  then "That query failed validation and was not run: <reasons>. Write a corrected query ...") for up to `IDENTITY_MCP_MAX_REPAIRS`
  (default 2) more attempts. If the model returns the same query twice it stops early. Every attempt is recorded in `attempts`, which
  the chat page shows as "Rejected attempts".

A typical repair: the model writes `WHERE department = 'Eng'`; grounding replies with the real department names; the
second attempt uses `'Engineering'`.

## Executing (`executor.py`)

`execute(sql, scope, inputs)` first prepends the `input` table definition if needed, then re-runs the static checks (nothing can
reach the database unvalidated), opens the sandbox connection, sets a 5 second deadline (a progress handler interrupts a slow query),
and fetches at most 200 rows. It returns `columns`, `rows`, `row_count` and `truncated`. Runtime failures become `QueryError` with
a sentence a user can read ("The query ran longer than 5 seconds ..."). Execution runs in a worker thread so the event loop is not blocked.

## Summarizing (`answer.py`)

`summarize(question, sql, result)` sends the question, the SQL and the first 50 rows with a short system prompt: be concise, quote
names and numbers exactly, list short results, say if results were truncated, attach each value to its column, never guess anyone's
gender ("they" or the name), and if several rows share a name, say so and tell them apart by email or id.

## The model client (`core/llm.py`)

A tiny async client for Ollama: `chat(messages, schema=...)` (non-streaming, temperature 0, optional JSON schema) and `embed(texts)`.
Settings: `IDENTITY_MCP_LLM_URL` (default `http://localhost:11434`), `IDENTITY_MCP_LLM_MODEL` (default `qwen2.5-coder:14b`),
`IDENTITY_MCP_EMBED_MODEL` (`nomic-embed-text`). Failures raise `LLMError`, which `ask` catches and reports.

## Logging (`core/logs.py`)

Each call gets a short request id; every log line carries it. Logger `identity_mcp.flow` writes one line per step (question,
retrieval hits with scores, generated SQL, validation outcome, repairs, execution time and row count, the answer, total time).
`identity_mcp.prompt` writes the full messages and raw replies (large; turn off with `IDENTITY_MCP_LOG_PROMPTS=0`). Logs go to
stderr so they never corrupt the MCP stdio stream.

## Example

```sh
uv run python -m identity_mcp.pipeline "How many users are terminated?"
```

prints a JSON document like:

```json
{
  "question": "How many suspended users have MFA disabled?",
  "answer": "0 suspended users have MFA disabled.",
  "sql": "SELECT COUNT(*) AS suspended_users_without_mfa FROM users WHERE status = 'suspended' AND mfa_enabled = 0",
  "examples": [{"id": "ex-078", "question": "How many privileged users lack MFA?", "score": 0.695}, "..."],
  "repairs": 0,
  "columns": ["suspended_users_without_mfa"], "rows": [[0]], "row_count": 1,
  "timings_ms": {"retrieve": 86, "generate_sql": 7264, "validate": 21, "execute": 1, "summarize": 1225}
}
```

The slow part is always the model: about 7 seconds to write the SQL, 1 second for everything else.

## How it fails

| Failure | Result |
|---------|--------|
| Ollama unreachable | `error` set; answer "I couldn't reach the language model: ..." |
| SQL still invalid after repairs | nothing runs; answer lists the problems found |
| unknown value (grounding) | nothing runs; answer "I couldn't find some of what you asked about in the data" |
| timeout / size limit | `QueryError` text as the answer |
| database file missing | "I generated a query but it failed to run" |
