# 4. Decisions and learnings

Why the system is built the way it is, what was tried and dropped, and what small local models taught us. Each entry says what
we chose, why, and what it cost.

## Design decisions

### The model proposes, code disposes
The model only writes SQL; plain code validates it and a read-only sandbox runs it. *Why:* a model can be wrong or manipulated, and
"delete everything" must be impossible, not merely discouraged. *Cost:* some correct-but-unusual queries are rejected.

### Retrieve solved examples, not documents
The retrieved material is question-to-SQL pairs; the schema is fixed text in every prompt. *Why:* the examples teach conventions
(how "today" is written, what "admin" means, one row per person) that no schema description conveys. This mirrors the production
system. *Cost:* the bank needs curating; the feedback loop is how it grows.

### Dense retrieval only
A keyword (BM25) hybrid was built and measured: 60/87 against 62/87 for dense alone, and a direct check showed dense retrieval already
finds a question's own entry 99% of the time. The hybrid was removed. *Lesson:* it was built on a guess that embeddings could not
tell domain words ("MFA", "orphaned") apart; measuring first would have saved the work.

### Qwen2.5-coder 14B as the default model
About 9 more cases right (of 77) than Llama 3.1 8B at roughly 1.8x the latency. The model is a setting (`IDENTITY_MCP_LLM_MODEL`);
Ollama is only the runner, so any pulled model works. Llama stays supported for small machines.

### Two scoped tools, client-side orchestration
`query_identity` and `query_activity`, each limited to its own tables. *Why:* it matches production (relational store plus search
index), where a cross-store question must be several calls. The data is in one SQLite file here, so the limit is enforced by a scope
in three places (prompt, validation, SQLite authorizer) rather than by physics. *Cost:* two-tool questions take 30 to 60 seconds.

### The hand-off passes a reference, not data
`result_id` plus `filter_from`, resolved by the server into an `input` table. *Why:* a model copying 72 ids between calls is slow and
error-prone. Only key columns (`id`, `*_id`, `email`) may be handed over because names repeat. A query that ignores `input` is rejected.

### Stateless server, client-held history
The client sends the last three turns with each request. *Why:* nothing to expire or clean up, any client can bring its own history,
and the server can be restarted freely. *Cost:* history is lost on page refresh, and there are no saved chats.

### A custom LangGraph graph, not a prebuilt agent
The loop needed rules the prebuilt `create_react_agent` does not give: explicit finishing, nudges, a step budget, recovery of text
tool calls, verification. A hand-written `StateGraph` (about 150 lines) made each rule a node. The prebuilt helper is also
deprecated. The MCP bridge is our own because `langchain-mcp-adapters` 0.3.1 does not import with MCP SDK 2.x.

### Explicit finishing with a `final_answer` tool
A loop that treats plain text as the answer stops half way when a small model writes "Now let's check their activity". Making
`final_answer` a tool call removes the ambiguity.

### Answers are checked by code, not by a second model
`verify.py` compares the answer with the tool results using plain rules. *Why:* small models grade themselves badly, and code is free,
fast and repeatable. *Cost:* it cannot see ambiguity inside the data itself.

### Nothing is promoted automatically from feedback
Thumbs go to a separate file; a person reviews; every promotion is re-validated and re-run. *Why:* a bank any click can write to is a
way to teach the model wrong answers.

### Layers with enforced direction
`tests/test_architecture.py` fails on a forbidden import. *Why:* layers erode quietly otherwise; a test makes the rule real. One file
per concern, docstrings on everything, so the code can be read.

### Fixed "today"
Everything uses 2026-11-01, written into prompts and validation. *Why:* answers and eval results must not change with the calendar.

## What small local models taught us

- **A prompt rule is not enough; enforce in code.** Rules the model ignored became checks: scoped tools return `id` first, a query
  must read `input`, finishing needs `final_answer`, literal values are grounded.
- **Error messages are prompts.** Terse validator output was ignored. Messages that name the offending reference and show where the
  right column lives were fixed on the first retry. Grounding lists the real values.
- **Models fail in different ways.** Qwen writes tool calls as JSON text (recovered in `recovery.py`). Llama fills optional nested
  objects with nulls (pruned) and announces steps instead of doing them (explicit finishing, nudges).
- **Small models apologise and act in the same message.** "I apologize... ```json {the tool call}```" is a tool call with prose around it; recovery has to look inside code fences, not only at replies that are pure JSON.
- **Reword nudges carefully.** A nudge that read as "do it again" made the model redo finished work (102 s); one that says "do not
  repeat a tool call that already succeeded" cut it to 49 s.
- **Examples help less than expected, in a lumpy way.** About 20 of the 93 eval cases change outcome between no examples and four, in
  both directions, so the net gain is small even though retrieval clearly works.
- **Measure before you build.** The hybrid retrieval detour, and a wrong claim that the dense and hybrid runs tied (a truncated
  paste dropped two cases), were both corrected by looking at the actual numbers.
- **Ambiguity lives in the data.** With 500 people drawn from 600 name combinations, names repeat. An answer can be word-for-word
  supported by the rows and still merge two people. Identify people by id or email.

## Useful facts about the toolchain

- MCP SDK 2.x: `MCPServer`, `Client`, `StdioServerParameters`. Tool descriptions must be passed as `description=`; an f-string
  docstring is not a docstring.
- LangGraph: identical message objects are merged by id in `add_messages`, so a scripted test model must return fresh copies.
- SQLite: an authorizer plus `query_only` and `mode=ro` gives defence in depth; `EXPLAIN` is a free dry run.
- `uv add --optional` re-syncs the environment and can drop extras installed earlier; keep agent packages in the dev group too.
