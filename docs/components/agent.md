# Agent: the LangGraph loop that chains the tools

**Files:** `src/identity_mcp/agent/` - `tools.py`, `graph.py`, `prompts.py`, `recovery.py`, `verify.py`, `trace.py`, `session.py`,
`__main__.py`. Needs `uv sync --extra agent`.

## What it is

A **client** of the MCP server, built with LangGraph. It is the part that decides *which tool to call next* and *when to stop*. In
production this role is played by a Vercel AI SDK client; here it is Python, to cover the same ground with LangGraph.

It talks to the server **only over MCP** (the layer rule forbids importing the pipeline or any server code), so it would work
against any MCP server with tools like these.

## The ReAct loop in one picture

*ReAct* = reason and act, repeatedly: the model looks at what it knows, either calls a tool or gives the answer, sees the tool's
result, and decides again.

```
START -> agent --(data tool call)-----------> tools -> agent ...
            |--(final_answer after data)-----> finish -> verify --(ok, or one revision used)--> END
            |                                              '--(answer contradicts the data)--> revise -> agent
            |--(plain text, or final_answer with no data yet)--> nudge -> agent    (at most 2 nudges)
            '--(wants another tool but budget spent)----------> give_up -> END
```

| Node | What it does |
|------|--------------|
| `agent` | calls the model (the two MCP tools and `final_answer` bound), repairs tool calls written as text, counts the call |
| `tools` | runs the data tools the model asked for through MCP and returns each result or error as a message |
| `finish` | ends with the text the model passed to `final_answer` |
| `verify` | checks that answer against the tool results (code, no model) |
| `revise` | sends a wrong answer back once with the problems listed |
| `nudge` | answers plain text, or an answer given before fetching data, by telling the model what to do |
| `give_up` | ends honestly when the step budget (6 model calls) is spent |

The state is just the message list plus counters (`steps`, `nudges`, `revisions`, `issues`), so a run is easy to inspect and to test
with a scripted model.

## Rules the loop adds, each from a real mistake

Small local models are good at the main idea and bad at discipline. Where a prompt rule was not enough, the rule is enforced in code.

| Problem seen | Fix |
|--------------|-----|
| Llama writes "Now let's check their activity" as text and the loop stopped half way | finishing is an explicit tool call, `final_answer`; plain text is nudged (max twice) |
| Qwen writes `{"name": "query_identity", "arguments": {...}}` as message text instead of a real tool call | `recovery.py` converts it into a real call, only for tool names we offered. It also rescues a call inside a code fence or `<tool_call>` tags with prose around it ("I apologize... ```json {...}```"), but leaves bare JSON mixed into a sentence alone |
| Llama sends `{"result_id": null, "column": null}` for an optional argument | `prune` removes nulls and empty strings before the call |
| the server's JSON schema (`$ref`, `anyOf [x, null]`) confuses small models | `flatten_schema` inlines references and drops optional wrappers |
| a failed tool call killed the run | errors come back as messages the model can read (`handle_tool_errors`) |
| the model keeps calling tools forever | 6 model calls per question, then `give_up` |
| the model answers from memory without calling a tool | a `final_answer` before any data is sent back with a nudge |
| `langchain-mcp-adapters` 0.3.1 fails to import with MCP SDK 2.x | our own small bridge in `tools.py` |

## The MCP bridge (`tools.py`)

`open_tools()` connects to the server (a stdio subprocess by default; a URL if `IDENTITY_MCP_AGENT_SERVER` is set), lists its tools,
and wraps each as a LangChain `StructuredTool` whose call goes through `Client.call_tool`. The result is returned to the model as
JSON text. The subprocess inherits the `IDENTITY_MCP_*` settings and quiet-logging defaults.

## The prompt (`prompts.py`)

Tells the model: today's date; the two tools and what each covers; always use a tool; ask each tool one clear, self-contained
question; a question needing both sides takes two calls (first tool, then the other with `filter_from = {result_id, column: "id"}`);
never guess anyone's gender (use the name or "they"); for "who has no activity" ask exactly that, do not subtract lists yourself; use `row_count` for totals; read errors and fix the next
call; never write what you are about to do, do it; earlier turns are only for working out references like "him"; finish with
`final_answer`, without mentioning SQL or result ids.

## Answer verification (`verify.py`)

The SQL is validated, but the sentence the model writes about the rows is not. A small model can state a total that was never in
the data, name someone who is not in the rows, or quietly lose rows from a list. After `final_answer` the `verify` node checks, in plain code:

1. **Numbers.** Every number in the answer must appear in the results (a row count, a cell value, a column's distinct count) or in a
   question that was asked ("the last 90 days"). Dates, times of day and list numbering are ignored.
2. **Listed names.** Every bullet or numbered item that looks like a name (short, capitalised, no colon) must match something in a
   result cell.
3. **Omissions.** If the answer lists at least half of the last result's names but not all of them, the missing ones are reported.
   An answer that names few or none is read as a summary ("15 accounts"), not a list.

Failure handling: the model gets **one** chance to fix it (`revise` shows it the exact problems); if the second answer is still wrong
it is sent with a visible caveat: *"(Note: this answer could not be fully checked against the data: ...)"* rather than passing as
checked. The result carries `verified` (true / false / none when not checked) and `issues`.

**What it cannot see:** a result that is itself ambiguous. "What groups is Elena Johnson in?" matches three different people named
Elena Johnson; the answer merges their groups, every word is in the data, and it passes. Showing email or id next to repeated names
is a separate fix.

## Events for the screen (`trace.py`) and the streamed session (`session.py`)

The graph reports LangChain messages; `trace.events_for` turns them into plain events (`tool_call`, `tool_result`, `nudge`,
`revise`, `verification`) and `session.events` adds a final `answer` or `error`. The stream never raises: a failure becomes an
`error` event. Both the web endpoint and the command line use these.

## Running it

```sh
uv sync --extra agent
uv run python -m identity_mcp.agent "Show me dormant admin accounts"
uv run python -m identity_mcp.agent --model llama3.1:8b --max-steps 8 "Who is in Cloud Admins and when were they last active?"
```

Each step prints to stderr as it happens; the answer goes to stdout:

```
[ 0.0s] model qwen2.5-coder:14b; tools: query_identity, query_activity
[10.9s] -> query_identity('Which active users have admin access?')
[19.8s] <- identity: 72 row(s), columns ['id', 'name', 'email'], result_id=r_046589
[25.1s] -> query_activity('Which of these users had no activity in the last 90 days?', filter_from=r_046589.id)
[36.1s] <- activity: 15 row(s), columns ['id', 'name', 'email'], result_id=r_f9876c
[43.3s] -> final_answer(answer='The following dormant admin accounts ...')
[43.3s] answer checked against the results: ok
```

Settings: `IDENTITY_MCP_AGENT_MODEL` (default: the SQL model), `IDENTITY_MCP_AGENT_SERVER` (URL of a running server), `--max-steps`.

## Measured

One question checked end to end (not an eval): dormant admins gives the same 15 people as a direct SQL join with both
`qwen2.5-coder:14b` (about 30 to 43 s) and `llama3.1:8b` (49 s). Each model call is 3 to 15 s, so a two-tool question takes 30 to 60
seconds. There are no agent eval cases yet.
