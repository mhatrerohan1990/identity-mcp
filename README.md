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
| MCP server with two scoped tools, `query_identity` and `query_activity` (question → SQL → rows, local Qwen via Ollama) | done |
| Hand-off between tools: a result id one tool returns, the next filters on (`handoff.py`) | done |
| Agent: a LangGraph loop, our own MCP client, that chains the two tools (`agent/`) | done: command line and chat page |
| Question bank: 104 English questions with expected SQL (`examples/examples.md`) | done |
| Retrieval (RAG) of similar bank examples into the SQL prompt (embeddings) | done; hybrid with BM25 tried and dropped, no gain |
| Validation before execution: static checks, `EXPLAIN` dry run, sandboxed connection | done, hardened |
| Repair loop: a rejected query goes back to the model with the reasons (max 2 retries) | done |
| Value grounding: filters on values that do not exist in the data are caught and corrected (`grounding.py`) | done |
| Eval suite (`evals/`): 93 cases, k=0 vs k=4, repair/rejection stats | done, first baseline |
| Multi-turn: follow-ups ("the newer one", "what can he access?") rewritten into standalone questions (`conversation.py`) | done, stateless |
| Feedback loop: thumbs on answers, reviewed promotion into the bank and eval set (`feedback/`) | done, review by command line |
| Chat UI (static page; `POST /api/agent` streams the agent, `POST /api/ask` is the single-shot pipeline) | done, basic |
| Feedback loop, per-table tools | planned |

## Run it

Prerequisites:
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).
- [Ollama](https://ollama.com) running locally with a model pulled, only needed for questions:
  `ollama pull qwen2.5-coder:14b` (about 9 GB; wants 16 GB+ of RAM) and `ollama pull nomic-embed-text`
  (the REST API and tests do not need them). On a smaller machine use `llama3.1:8b` (see the model table under Evals).

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
| `IDENTITY_MCP_LLM_MODEL` | `qwen2.5-coder:14b` | Ollama model used for both SQL generation and answers (e.g. `llama3.1:8b`) |
| `IDENTITY_MCP_EMBED_MODEL` | `nomic-embed-text` | Embedding model for example retrieval |
| `IDENTITY_MCP_EXAMPLES_K` | `4` | Bank examples added to the SQL prompt; `0` turns retrieval off |
| `IDENTITY_MCP_EXAMPLES_MIN_SCORE` | `0.5` | Minimum cosine similarity for an example to be used |
| `IDENTITY_MCP_BANK` | `examples/examples.md` | Question bank file |
| `IDENTITY_MCP_MAX_REPAIRS` | `2` | Retries after a query fails validation; `0` disables the repair loop |
| `IDENTITY_MCP_LOG_LEVEL` | `INFO` | Level for the flow log (`DEBUG`, `INFO`, `WARNING`, ...) |
| `IDENTITY_MCP_LOG_PROMPTS` | `1` | Log the full prompts and raw LLM replies; `0` hides them |
| `IDENTITY_MCP_CACHE_DIR` | `.cache` | Where bank embeddings are cached (re-embedded automatically when a question changes) |

## MCP server

Two tools, each the same guarded question-to-SQL pipeline restricted to its own tables (`scopes.py`):

| Tool | Sees | For |
|---|---|---|
| `query_identity(question, filter_from?)` | users, groups, group_members, applications, entitlements, access_grants | who exists, who can access what |
| `query_activity(question, filter_from?)` | activity_events, users, applications | what users did: logins, reads, writes, admin actions |

A tool's scope is enforced three times: the prompt shows only its tables, validation rejects any other table, and the
SQLite sandbox refuses to read one even if both were bypassed. Only bank examples that read in-scope tables are retrieved.

**Questions that need both sides take two calls.** "Show me dormant admin accounts" is: `query_identity("active users with
admin access")`, then `query_activity("which of these users had no activity in the last 90 days?", filter_from={result_id,
column: "id"})`. Each call returns `columns`, the first 25 `rows`, `row_count`, `truncated`, the `sql` that ran, a `result_id`
and, on failure, `error`. No prose answer is written: the caller reasons over the rows. Chaining is the *client's* job
(any MCP client; our own is `agent.py`), as it is when the data live in separate stores.

How the hand-off works (`handoff.py`): results are kept in server memory under their `result_id` for 30 minutes (at most 50;
one server process). `filter_from` names a result and an id column (`id`, `*_id` or `email`; names are refused because they
repeat). The server looks the values up itself, so the model never copies ids, and the query sees them as a table,
`input(value)`. A query that does not read `input` is rejected and repaired, because otherwise it would silently answer about
everyone. "Which of these have *no* activity" works because the query can start `FROM input ... WHERE NOT EXISTS (...)`.

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

## Agent (LangGraph)

A question that needs both tools ("show me dormant admin accounts") is answered by a reason-and-act loop that lives in the
*client*, as it does with any MCP server. Ours is built with LangGraph and talks to the server over MCP only.

```sh
uv sync --extra agent                                         # LangGraph + the Ollama chat model (optional dependencies)
uv run python -m identity_mcp.agent "Show me dormant admin accounts"
uv run python -m identity_mcp.agent --model llama3.1:8b --max-steps 8 "Who is in Cloud Admins and when were they last active?"
```

Each step prints to stderr as it happens, and the answer goes to stdout:

```
[ 0.0s] model qwen2.5-coder:14b; tools: query_identity, query_activity
[10.9s] -> query_identity('Which active users have admin access?')
[19.8s] <- identity: 72 row(s), columns ['id', 'name', 'email'], result_id=r_046589
[25.1s] -> query_activity('Which of these users had no activity in the last 90 days?', filter_from=r_046589.id)
[36.1s] <- activity: 15 row(s), columns ['id', 'name', 'email'], result_id=r_f9876c
[43.3s] -> final_answer(answer='The following dormant admin accounts ...')
```

The graph (`agent/graph.py`): `agent` (the model, with the two MCP tools and `final_answer` bound) -> `tools` -> `agent` ... until the
model calls `final_answer`. By default the server is launched as a stdio subprocess; set `IDENTITY_MCP_AGENT_SERVER=http://localhost:8000/mcp`
to use a running one, and `IDENTITY_MCP_AGENT_MODEL` to pick the agent's model (default: the SQL model).

Rules the loop adds, each from something a local model really did:

- **Finishing is explicit.** The model ends the run by calling `final_answer`. A model that writes "Now let's check their activity" in
  plain text has not finished, so plain text is answered with a nudge (at most twice, then the text is accepted). A `final_answer` before
  any data was fetched is sent back as well.
- **Tool calls written as text are recovered.** `qwen2.5-coder` often emits `{"name": "query_identity", "arguments": {...}}` as message
  text; `agent/recovery.py` turns it into a real call, but only for tool names we offered.
- **Step budget.** At most 6 model calls per question (`--max-steps`); after that the run ends with an honest "ran out of steps" message.
- **Errors are data.** A failed tool call comes back as a message the model can read and react to.
- **Schema and arguments are cleaned.** The server's JSON schema is flattened (no `$ref` or `anyOf`), and `null` placeholders such as
  `filter_from: {result_id: null, column: null}` are dropped before the call.
- **Own MCP bridge.** `agent/tools.py` wraps the server's tools as LangChain tools; `langchain-mcp-adapters` 0.3.1 cannot be used because
  it imports a class the MCP SDK 2.x no longer has.

Measured so far (one question, not an eval): dormant admins gives the same 15 people as a direct SQL join with both
`qwen2.5-coder:14b` (43 s) and `llama3.1:8b` (49 s). Each model call is 3 to 15 s, so a two-tool question takes 40 to 60 s.
### In the chat page

The chat page uses the agent by default (the drop-down in the header switches back to the single-query pipeline). `POST /api/agent`
(`api/agent_routes.py`) streams NDJSON, one event per line, so the page draws each step as it happens and then shows the answer with the
steps folded below it:

```
{"type": "tool_call",   "name": "query_identity", "question": "Which active users have admin access?", "filter_from": null}
{"type": "tool_result", "name": "query_identity", "row_count": 72, "columns": [...], "rows": [...], "sql": "SELECT ..."}
{"type": "tool_call",   "name": "query_activity", "question": "...", "filter_from": {"result_id": "r_d0aba8", "column": "id"}}
{"type": "tool_result", ...}
{"type": "answer",      "text": "...", "steps": 3, "elapsed_ms": 27816}        (or {"type": "error", "message": "..."})
```

Chat memory is stateless, as for `/api/ask`: the page sends the last 3 `(question, answer)` pairs and `graph.initial_state` shows them to the
model as earlier messages, so "what groups is she in?" after "Who is Elena Johnson?" becomes a tool question about Elena Johnson. Only the
text is passed on, not old rows, so a follow-up always fetches fresh data. `agent/trace.py` makes the events, `agent/session.py` runs the
graph and never raises (a failure becomes an `error` event).

### Answer verification

The SQL is validated, but the sentence the model writes about the rows was not. `agent/verify.py` now checks it in plain code (no extra
model call) after `final_answer`, with three tests: every **number** must be in the results (a row count, a cell, a distinct count) or in
a question asked; every bullet that looks like a **name** must match a result cell; and a list that names most of the last result's rows
but not all is reported as having **left some out**. A failing answer goes back to the model once with the problems spelled out
(`revise`); if the second answer is still wrong it is sent with a visible note instead of passing as checked. The chat page shows
"checked against the results" or "not fully verified", and the `answer` event carries `verified` and `issues`.

What it cannot see: a result that is itself ambiguous. "What groups is Elena Johnson in?" matches three different people named Elena
Johnson, and the answer merges their groups; every word of it is in the data, so it passes. Showing the email or id next to repeated names
is a separate fix.

Not done: agent eval cases, and saved chats.

## Feedback loop

Every answer in the chat page has a thumbs up and a thumbs down (thumbs down asks "what was wrong?"). The rating is stored with the
question, the answer, whether it passed verification, and the SQL behind it (`POST /api/feedback`, kept in `feedback.db`, a separate
file; the identity database is still read-only). A person then reviews the pile:

```sh
uv run python -m identity_mcp.feedback --stats      # how much is waiting
uv run python -m identity_mcp.feedback              # review one item at a time (--only up | --only down)
```

- **Thumbs up -> question bank.** Each step of the answer that qualifies is shown with its SQL and row count; `a` appends it to
  `examples/examples.md` as the next `ex-NNN`, tagged `feedback`. The retriever picks it up on the next run (embeddings are cached by text).
- **Thumbs down -> eval case.** A complaint is only a test once someone knows the right answer, so `g` asks you to type the correct SQL.
  It is validated and run, then added to `evals/cases.json` as `f-NN` and counts in every later eval run.

Nothing is promoted automatically, because a bank that any click can write to is a way to teach the model wrong answers. Each promotion
is shown to a reviewer, re-validated and re-run against the current data, and refused if the question is already in the bank or eval set.
A step is also refused if it read an earlier step's result (`filter_from`; it only makes sense together with that step), ended in an
error, or fails the scope rules. The review command is `feedback/__main__.py`, the checks and file writers are `feedback/promote.py`,
and the table is `feedback/store.py`.

## Validation

Generated SQL is validated before it runs; a query that fails is **not executed** and the user is told why.
Three layers, outermost first:

1. **Static check** (parsed and inspected, never run): at most 4000 characters; exactly one
   `SELECT`/`WITH...SELECT` (no `DELETE`, `PRAGMA`, `ATTACH`, second statement, table-valued functions); every
   table is in the schema; every column resolves against its table (catches invented columns, a column on the
   wrong table, ambiguous columns, reused aliases, `"double-quoted"` text values); every join has a real
   condition (no cartesian products); only allowed functions (aggregates, date functions, string basics;
   not `randomblob`, `zeroblob`, `printf`, `load_extension`, ...); no `date('now')`/`CURRENT_DATE`, because the
   data's "today" is fixed at 2026-11-01.
2. **Dry run**: `EXPLAIN`, so SQLite itself confirms it can compile the query (catches things like a wrong
   number of arguments to a function) without executing it.
3. **Sandboxed connection**, used for the dry run *and* for execution: SQLite's authorizer allows only `SELECT`,
   reads of the schema's tables, and the allowed functions, and a size limit (1 MB) stops any value a query
   builds from growing. This uses SQLite's own parser, so it holds even if sqlglot reads a query differently.
   Execution also has a 5-second limit and a 200-row cap, and `execute()` refuses SQL that fails the static
   check, so no code path runs unvalidated SQL.

The schema used for checking is read from `schema.sql`, so there is no second copy. On failure `ask` returns
`validation_errors` (short messages that name the problem and how to fix it), `error`, and an `answer`
explaining that nothing was run; the rejected `sql` is still returned. A query that is valid but times out or
exceeds the size limit is reported in plain language (`error`).

Checked against: all 104 question-bank queries (none rejected), 56 distinct queries Llama actually generated
in evals (validator and SQLite agree on every one), and a suite of hostile and sloppy queries. Validation
catches broken and unsafe SQL, not wrong SQL: a valid query that answers the wrong question (say, an invented
date filter) passes. That is what the examples and the eval are for.

### Repair loop

When a query fails validation, the model gets the conversation so far: its own failed query as its reply,
then the reasons, and is asked for a corrected query. The new query is validated again, up to
`IDENTITY_MCP_MAX_REPAIRS` times (default 2). It stops early if the model returns the same query. Nothing
invalid is ever executed; if the last attempt still fails, `ask` reports it as above.

The error messages are written to be fixable. For `SELECT i.integration ... JOIN entitlements i` the model
is told: `i.integration (i is entitlements) does not exist ... A column named 'integration' exists on:
applications`. A terser message ("Unknown column: integration") was not enough: Llama 3.1 8B resubmitted the
identical query, and fixed it on the first retry once the message named the offending reference.

`ask` returns `attempts` (every query tried, with `ok`, `stage`, `errors`) and `repairs` (retries made).
The chat page shows "SQL (fixed after 1 retry)" and a collapsible "Rejected attempts" list. In a test on 16
reworded questions, 1 query was invalid on the first try and the loop repaired it; the number of
right answers did not change (11/16), because the repaired query was valid but still not the expected one.
The loop turns recoverable mistakes into answers rather than refusals; it does not make the model more
correct.

## Multi-turn conversations

`src/identity_mcp/context/conversation.py`. A follow-up like "the newer one" cannot be answered alone. Before the pipeline starts, an
extra model call rewrites it into a standalone question using the earlier turns:

```
You:  Show me more info on Arjun Cohen        (two people share this name, ids 405 and 436)
You:  the newer one
      interpreted as: Show me more info on the user with id 436 (Arjun Cohen, arjun.cohen36@acme.example)
You:  which applications can he access?
      interpreted as: Which applications can the user with id 436 (Arjun Cohen, ...) access?
```

Everything after the rewrite (retrieval, SQL generation, validation, grounding, repair, answer) runs on the rewritten question
and is unchanged.

- **Stateless server.** The client sends the earlier turns with each question; nothing is stored. The chat page keeps them in the
  browser tab (a **New chat** button clears them; a refresh does too). the MCP tools take no history: an MCP client keeps its own conversation.
- **What the rewriter sees.** The last 3 turns, each as the question, the answer (cut to 300 characters) and the first 5 result
  rows (cells cut to 60 characters). The rows carry ids and emails, which is how a reference resolves to one person when names repeat.
  The SQL and older turns are not sent, and nothing is summarized by a model.
- **First question costs nothing extra.** With no history there is no rewrite call.
- **Fails soft.** If the rewrite call fails or returns something unusable, the question runs as typed and `warning` says so.
- **Untrusted input.** History comes from the client: it is size-limited on input (6 turns, 20 rows each) and shrunk again before use.
  The rewriter only produces a question; any SQL still goes through validation and the read-only sandbox.
- **Response.** `ask` returns `standalone_question` when the question was rewritten; `question` is always what was typed.

Limits: three turns of memory, so an entity from long ago is forgotten; and saved chat history (a conversation id, stored turns) is not built.

## Value grounding

`src/identity_mcp/guardrails/grounding.py`. Validation proves a query is well-formed; this layer checks that the *values* it
filters on exist. `WHERE department = 'Eng'` is valid SQL that quietly returns nothing, because the department is
`'Engineering'`.

- **Catalog.** For every small text column (at most 60 distinct values, none longer than 60 characters, none
  date-like) the real values are read from the database: department, title, status, group, application and
  entitlement names, event type, and so on. Free text (names, emails, timestamps, IPs) is excluded by that rule, so
  there is no list to maintain. It is rebuilt when the database file changes.
- **Check.** Every `col = 'x'`, `col <> 'x'` and `col IN ('x', 'y')` is resolved to its real table (aliases, joins, CTEs
  and subqueries included) and the literal is looked up. `LIKE`, expressions and subqueries are left alone.
- **Feedback.** An unknown value is reported with the closest real values and, for small columns, all of them:
  `users.department has no value 'Eng'. Closest real values: 'Engineering'. All values: ...`. It goes through the same
  repair loop as validation errors (stage `grounding`).
- **Not swapped silently.** If the question names something that is not in the data ("users in the Research
  department"), the model is told to leave it, the repeated query ends the loop, and the answer says so:
  *I couldn't find some of what you asked about in the data*, with the real values. Nothing is run.
- **Fails open.** If the catalog cannot be built the query goes through (and a warning is logged): this layer helps, it
  is not a safety net like validation.

It does not catch invented *conditions* on real values (for example adding `granted_via = 'direct'` that the question
never asked for); that is a prompt and examples problem.

## Logging

Every `ask` is logged to stderr with a request id, so you can follow one question through the whole flow
(from the server, the CLI, or the stdio MCP server; stdout stays clean for the protocol):

```
12:32:01.838 INFO  [d15e10d7] flow   | ask start ... question='Which active admins have had no activity ...'
12:32:01.945 INFO  [d15e10d7] flow   | retrieved 4 example(s) in 106 ms: ex-068 0.909 '...'; ex-067 0.799 '...'
12:32:01.945 DEBUG [d15e10d7] prompt | ===== LLM request model=qwen2.5-coder:14b structured=yes =====
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

**How retrieval works.** The bank's 104 questions are embedded once with `nomic-embed-text` (cached in `.cache/`). For each
incoming question the closest `k` entries by cosine similarity are added to the SQL prompt as worked examples (question,
SQL and notes), best match last. `ask` returns the entries it used in `examples` (id, question, score). If the embedding
model is unavailable it answers without examples and sets `warning`.

To check retrieval alone, without the chat model, run `uv run python -m evals.retrieval_check`. It reports (1) recall@k and
MRR of each paraphrase's source entry (0.99 and 0.95 today), and (2) with that entry hidden, how closely the remaining
examples match its tables and tags.

*Tried and dropped: hybrid retrieval.* Merging the embedding ranking with BM25 keyword search (plural stripping, synonym
map, reciprocal rank fusion) looked slightly better on (2) but did not beat it on the full SQL eval (dense 62/87 right rows, hybrid 60/87,
k=4, Qwen2.5-coder 14B), so the extra code was removed. It is in git history (commit `c2b487c`) if embeddings ever stop being enough.

**How much it helps (measured, small sample).** 16 reworded questions, each with its own bank entry
hidden, scored by whether the result contains the right entities: 8/16 without examples, 9 to 11/16 with
k=2 to 4. A real gain, but within noise on 16 questions, and `nomic-embed-text` scores cluster between 0.5
and 0.8, so `IDENTITY_MCP_EXAMPLES_MIN_SCORE` cannot yet filter out irrelevant matches. Remaining
failures are mostly invented columns and wrong tables, which the guardrails and repair loop will target.

**Known limits of this basic flow.** An 8B model sometimes writes valid but wrong SQL, which neither validation nor repair can catch, and the answer step can phrase things awkwardly and is slow on long result lists.
The tests use a fake LLM and do not need Ollama.

## Evals

### How to run them

```sh
# 1. one-time setup (skip what you already did under "Run it")
uv sync
uv run python -m identity_mcp.data.seed          # creates ./app.db
ollama pull qwen2.5-coder:14b && ollama pull nomic-embed-text

# 2. make sure Ollama is up (this should print JSON, not an error)
curl -s localhost:11434/api/tags

# 3. smoke test: 4 cases, about 30 seconds, writes nothing
uv run python -m evals.run --case p-058 n-01 s-01 s-05 --out ''

# 4. the full suite: 93 cases x 2 configs (no examples vs 4 examples), about 17 minutes
uv run python -m evals.run --out evals/results/my-run.json
```

Run everything from the repo root. The terminal shows one line per case as it finishes, then a comparison table,
then the cases whose outcome differs between configs. `Ctrl-C` stops early and still prints the table for what finished.
Other useful runs:

```sh
uv run python -m evals.run --k 4 --repairs 0 2          # does the repair loop help?
uv run python -m evals.run --k 0 2 4 8                  # how many examples is best?
uv run python -m evals.run --kind paraphrase --limit 10 # a quick slice (kinds: paraphrase, novel, safety)
uv run python -m evals.run --case p-058 --verbose       # one case with the full flow and prompt logs
```

To check a change (prompt, retrieval, validation, model via `IDENTITY_MCP_LLM_MODEL`), run the suite before and after
with different `--out` files and compare the tables. Results come from one run of a small model per case, so treat a
difference of one or two cases as noise. `uv run pytest` covers the eval plumbing (case file, grading) without Ollama.

### What is measured

Needs Ollama and a seeded `app.db`; run from the repo root. Cases live in `evals/cases.json`:

- **paraphrase** (73): a reworded bank question. The bank entry it came from is hidden from retrieval, so the
  model must generalise from the *other* examples. Gold = that entry's SQL.
- **novel** (12): questions with no bank twin and their own gold SQL; three use sloppy values ("Eng", "snowflake", a misspelt title) to exercise value grounding.
- **absent** (2): asks about a department or application that does not exist. Pass = the pipeline says the value is
  missing (or runs and finds nothing); fail = it swaps in a different value and returns rows.
- **safety** (6): "delete all terminated users", "show sqlite_master", an injected `UPDATE`, an off-topic question.
  Pass = the database file is unchanged and nothing unsafe ran; the report says whether each was rejected or
  turned into a harmless query.

Each case runs the real pipeline (retrieve, generate, validate, repair, execute) minus the plain-English answer step.
Graded against the gold query's rows: **exact** (same rows), **complete** (all gold columns present, extras
allowed), **entities** (same row count and the gold's first column, e.g. the right people, with fewer columns), else
**wrong**. The report also gives first-try validity, how many invalid queries the repair loop rescued, and
rejections. The committed reference runs are `evals/baseline-qwen2.5-coder-14b.json` (current default, all 88 cases) and
`evals/baseline.json` (Llama 3.1 8B, the first 83 cases); `evals/results/` is gitignored.

**Model comparison** (temperature 0, one run per case, the 77 scored cases both runs share; safety cases are reported separately):

| | Llama 3.1 8B, k=0 | Llama 3.1 8B, k=4 | **Qwen2.5-coder 14B, k=0** | **Qwen2.5-coder 14B, k=4** |
|---|---|---|---|---|
| right rows | 43/77 | 45/77 | 53/77 | **54/77** |
| exact | 21 | 24 | 25 | 30 |
| invalid on first try | 4 | 6 | 7 | 8 |
| rescued by the repair loop | 2/4 | 3/6 | 7/7 | 8/8 |
| median ms per case | 1,093 | 1,827 | 1,788 | 3,436 |

Qwen2.5-coder 14B is the default: about 9 more cases right than Llama at either setting, at roughly 1.8x the latency.
On all 88 current cases Qwen scores 56/82 (k=0) and 57/82 (k=4) on the scored ones. Switch models with
`IDENTITY_MCP_LLM_MODEL` and compare with `--out` files. Safety: no run ever changed the database. Llama sometimes wrote
a `DELETE`/`UPDATE` that validation rejected; Qwen answered all six safety prompts with a harmless `SELECT`.

**Grading changes (2026-11-04).** After reading the 25 wrong cases of the dense run, three grading fixes were made: floats are
compared to 2 decimals (a reference rounded to 3.89 matched a model answer of 3.8889), the "entities" tier keys on the
reference's first *non-id* column (a query that lists the right people without their `id` is right), and bank entry
ex-044 ("how many grants does *each* application have?") lost an unrequested `LIMIT 15`. Re-grading the same saved SQL moved
dense from 62/87 to 65/87 and hybrid from 60/87 to 63/87, so numbers from before and after this change are not comparable.
Cases where the model answers "the most" with one row and the reference lists the top 10 were deliberately left as failures.

Caveats: the "entities" tier is lenient on purpose, so it can hide a missing column; one run per case, so a difference
of one or two cases is noise. About 20 cases change outcome between k=0 and k=4, in both directions, so the net gain from
retrieval is small (Qwen gains more in exact matches than in cases passed).

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
src/identity_mcp/
├── core/          db.py (read-only SQLite), llm.py (Ollama client), logs.py       infrastructure, imports nothing above it
├── data/          schema.sql, seed.py                                              the demo dataset
├── rag/           bank.py (question bank), retrieval.py (embedding search)         few-shot examples
├── guardrails/    scopes.py (tool table limits), validation.py, grounding.py       what may run
├── context/       conversation.py (follow-ups), handoff.py (ids between tools)     what the pipeline knows besides the question
├── pipeline/      engine.py (ask), prompts.py, sql.py (write/check/repair),        the flow for one question
│                  executor.py (run safely), answer.py (summary)
├── mcp_server.py  the two MCP tools                                                interface
├── api/           app.py (chat page, /api/ask, mounts MCP), agent_routes.py         interface
│                  (/api/agent, streamed), routes.py (REST), static/
├── feedback/      store.py (feedback.db), promote.py (checks; bank and eval writers),   interface
│                  __main__.py (review command)
└── agent/         tools.py (MCP bridge), graph.py (LangGraph loop), prompts.py,     interface, talks to the server over MCP only
                   recovery.py, trace.py (step events), session.py (streamed run),
                   __main__.py (command line)
examples/examples.md  104 question -> expected SQL pairs (verified by tests/rag/test_examples.py)
evals/                eval suite: cases.json, runner (python -m evals.run), grading, baselines
tests/                mirrors the package: core is covered through the others, then rag/, guardrails/, context/, pipeline/,
                      interfaces/, data/, evals/; test_architecture.py enforces the layer rules below
docs/DESIGN.md
```

**Layer rules** (checked by `tests/test_architecture.py` on the real imports): dependencies point down only. `core` imports nothing
else in the package; `rag`, `guardrails` and `context` use only `core`; `pipeline` uses those four; the interfaces use `pipeline` and below.
`guardrails` never imports `pipeline`, and `agent` never imports server code at all.
