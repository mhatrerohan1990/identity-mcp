# Context: follow-up questions and the hand-off between tools

**Files:** `src/identity_mcp/context/conversation.py`, `context/handoff.py`

"Context" is everything the pipeline knows besides the question text. There are two kinds: what was said earlier in the chat
(`conversation.py`) and what an earlier tool call returned (`handoff.py`).

---

## Conversation: making a follow-up stand alone

### The problem

"Can I get more info on Arjun Cohen?" works. "Show me more about him" does not: retrieval and SQL generation see only the words
"him" and "more". Rather than make every later layer aware of conversations, this layer fixes the question *before* the pipeline
starts.

### How it works (`standalone_question`)

1. The client sends the earlier turns with the request. The server stores nothing (**stateless**: no session to expire, nothing to
   clean up, and any client can supply its own history).
2. History is untrusted, so it is limited on arrival (the `Turn` model) and shrunk again before use: the **last 3 turns**, each as
   its question, a short answer, and the **first 5 result rows** with each cell cut to 60 characters.
3. One model call (a small JSON schema `{"question": string}`) rewrites the new message into a standalone question.
4. The pipeline continues with the rewritten text. The result carries both `question` (what the user typed) and
   `standalone_question` (what was actually run); the chat page shows "Interpreted as: ...".

The rows are included because they carry ids and emails. That is how "the second one" or "the newer one" becomes a specific person
even when two people share a name.

### Example

Earlier turn: *"Show me the top 10 active users"* with rows including `(436, Arjun Cohen, arjun.cohen36@acme.example)`.
New message: *"Can I get more info on arjun?"*

Rewritten: *"Show more info on the user with id 436 (Arjun Cohen, arjun.cohen36@acme.example)"*

### Fails soft

If the rewrite call fails or returns something unusable, the original question is used and the result carries a `warning`. The
rewriter only produces a *question*; whatever SQL follows still goes through every guardrail.

### In agent mode

The agent does not use this layer. It receives the last three `{question, answer}` pairs as ordinary chat messages and the model
resolves "she" itself when it writes the tool question (see [agent.md](agent.md)). The two designs solve the same problem at
different levels: single-query mode rewrites, agent mode lets the agent's model do it.

---

## Hand-off: passing one tool's result to the next

### The problem

"Show me dormant admins" needs identity data (who are the admins) and activity data (who did nothing for 90 days). With separate
stores no single query can join them. The second step must filter on the first step's ids. Making the calling model copy 72 ids from
one tool call into the next is slow, costs tokens and invites mistakes.

### The mechanism

1. **Store.** A tool that returns rows also returns a short `result_id` (e.g. `r_f0cddb`). The full result (up to 200 rows) is kept in
   an in-memory `ResultStore` (`handoff.STORE`) for 30 minutes, at most 50 results.
2. **Reference.** The next call passes `filter_from = {"result_id": "r_f0cddb", "column": "id"}` instead of the ids. The *server* looks
   the values up itself (`STORE.values`), at most 500 of them.
3. **Table.** The values reach the SQL as a table named `input` with one column `value`. The pipeline prepends
   `WITH input(value) AS (VALUES (169), (70), ...)` to the model's query (`with_input`), then validates and runs the whole thing. The
   prompt gains one rule explaining what `input` is (`describe`).

Because `input` is an ordinary table, both questions work:

```sql
-- "which of these users had activity?"
SELECT ... FROM users u WHERE u.id IN (SELECT value FROM input) AND EXISTS (...)
-- "which of these users had NO activity?"   (a set difference no single scoped tool could do)
SELECT ... FROM users u WHERE u.id IN (SELECT value FROM input) AND NOT EXISTS (...)
```

### Safeguards

| Safeguard | Why |
|-----------|-----|
| a query that never reads `input` is rejected (`unused_input`) | otherwise the model could ignore the earlier result and answer a different question |
| only **key columns** may be handed over: `id`, `*_id`, `email` (`is_key_column`) | names repeat, so passing names would silently merge different people |
| unknown or expired id, missing column, too many values -> `HandoffError` text returned as `error` | the agent reads it and reruns the first step |
| values are written as SQL literals with quotes doubled (`_literal`) | the ids are data, never executable text |
| scoped tools always return `id` as the first column (prompt rule) | so there is always a column to hand over |

### Limits

The store lives in one server process's memory: shared by the two tools of that process, but not across several worker processes
or restarts. With a stdio MCP server that is one process per client, which is how the agent uses it.
