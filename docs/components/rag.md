# RAG: the question bank and retrieval

**Files:** `examples/examples.md`, `src/identity_mcp/rag/bank.py`, `rag/retrieval.py`, `core/llm.py` (the embedding call)

## What RAG means here

*Retrieval-augmented generation* = look something up first, then put it in the prompt. Our "something" is not a document. It is a
**solved example**: an English question plus the exact SQL that answers it on this schema. A language model writes much better SQL
when it sees two or three similar solved questions first (this is "few-shot prompting"), because the examples teach it our
conventions: how to count, which columns to return, how "today" is written, how admin access is defined.

Retrieval is the part that picks *which* examples to show. We do not show all 104 every time: too long, and unrelated examples
distract a small model.

## The question bank (`examples/examples.md`)

104 curated entries (and growing through the feedback loop). Each is Markdown in a fixed shape:

````markdown
## ex-003
**Q:** How many users are terminated?
**Tags:** users, count, status, terminated

```sql
SELECT COUNT(*) AS terminated_users FROM users WHERE status = 'terminated'
```
````

Optional `**Notes:**` explain a convention and are shown to the model with the example. Conventions all entries follow:
SQLite dialect, one `SELECT`; "today" is `date('2026-11-01')`; "admin" is `entitlements.is_admin = 1`; people questions return one row per
person; access counts direct and group grants; readable columns (names, emails). `tests/rag/test_examples.py` runs every entry
against the seeded database, so a wrong example cannot get in unnoticed.

`bank.py` parses the file into `Example` objects (`id`, `question`, `tags`, `sql`, `notes`).

## How retrieval works (`rag/retrieval.py`)

Step by step, with an example question: **"Which admins have no MFA?"**

1. **Embed the bank (once).** Every bank question is sent to the embedding model (`nomic-embed-text` via Ollama), which returns a list
   of 768 numbers representing its meaning. These are cached on disk (`.cache/bank_embeddings.json`, keyed by a hash of model +
   text), so only new or edited questions cost a model call.
2. **Embed the user's question.** One embedding call, with the prefix `search_query:` (the bank uses `search_document:`, which
   `nomic-embed-text` requires).
3. **Score.** All vectors are scaled to length 1, so a dot product equals *cosine similarity*: 1.0 means same direction (same
   meaning), 0 means unrelated. One matrix multiplication scores the whole bank.
4. **Filter and pick.** Drop entries below `MIN_SCORE` (0.5), skip excluded ids, take the top `K` (4).
5. **Order.** The prompt builder puts the best match **last**, nearest the question, where small models pay most attention.

A real result: for "How many suspended users have MFA disabled?" retrieval found `ex-078` (0.695), `ex-013` (0.692), `ex-012` (0.685),
`ex-082` (0.681). None is an exact twin, which is the point: the model sees *patterns* (how MFA is queried) and adapts them.

The in-memory index rebuilds itself when the bank file changes (modification time) or the embedding model changes, so a promoted
feedback entry is picked up without restarting.

## Scope-aware retrieval

When a question runs under a scope (identity or activity tool), bank entries whose SQL reads tables outside that scope are hidden
from retrieval (`engine._outside_scope`). Showing the activity tool an example that joins `access_grants` would only tempt it to write
a query that validation must reject.

## What retrieval does not do

- It does not retrieve schema or documentation; the schema is fixed text in every prompt.
- It does not rank by tags or keywords. A keyword (BM25) hybrid was built and measured, then removed: it scored 60/87 against dense-only
  62/87 on the eval suite, and a check showed dense retrieval already finds a question's own bank entry 99% of the time.

## Example (from Python)

```python
import asyncio
from identity_mcp.rag import retrieval
hits = asyncio.run(retrieval.retrieve("Which admins have no MFA?", k=3))
for h in hits:
    print(round(h.score, 3), h.example.id, h.example.question)
```

## How it fails

If Ollama is down or the bank file is missing, `engine.ask` catches the error, continues with **no examples** and adds a `warning` to
the result ("Answered without example retrieval"). Quality drops (the evals measure how much: no examples vs four) but the system
still answers.

## Tuning knobs

`IDENTITY_MCP_EXAMPLES_K` (default 4), `IDENTITY_MCP_EXAMPLES_MIN_SCORE` (0.5), `IDENTITY_MCP_EMBED_MODEL`, `IDENTITY_MCP_BANK`,
`IDENTITY_MCP_CACHE_DIR`. `uv run python -m evals.retrieval_check` measures retrieval alone (does the right entry come back?).
