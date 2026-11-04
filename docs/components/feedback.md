# Feedback loop

**Files:** `src/identity_mcp/feedback/` - `store.py`, `promote.py`, `__main__.py`; `api/feedback_routes.py`; the 👍/👎 code in
`api/static/index.html`

## Why

The question bank is hand-written and the eval set was written by the builders. Real users ask things nobody anticipated. The
feedback loop turns real use into improvements: good answers become **teaching examples**, bad ones become **tests**.

```
  chat page
     | 👍 / 👎 (+ the SQL behind the answer)
     v
  POST /api/feedback --> feedback.db          (a separate, writable file; the identity data stays read-only)
                              |
        python -m identity_mcp.feedback        a person reviews each item, one at a time
              |                          |
        👍 `a` = add                 👎 `g` = give the correct SQL
              v                          v
   examples/examples.md            evals/cases.json
   (ex-NNN, tag "feedback")        (f-NN, kind "novel")
   retrieval can now show it       every later eval run includes it
```

## Storing (`store.py`)

One SQLite table, `feedback`: time, rating (+1/-1), question, answer, comment, mode (`agent`/`single`), `verified`, `steps` (JSON), and
`status` (`new` -> `promoted` or `dismissed`). A `Step` is one query behind the answer: tool, question, sql, `linked` (it read an
earlier step's result), error. Location: `$IDENTITY_MCP_FEEDBACK_DB` or `feedback.db` (gitignored).

## Reviewing (`python -m identity_mcp.feedback`)

```sh
uv run python -m identity_mcp.feedback --stats       # {'total': 3, 'up': 2, 'down': 1, 'new': 3}
uv run python -m identity_mcp.feedback               # review everything pending (--only up | --only down)
```

A thumbs-up session looks like:

```
#1 thumbs up (agent, 2026-11-04T18:51:39Z)
  Q: How many active users have MFA disabled?
  A: 12

  step (query_identity): How many active users have MFA disabled?
  SELECT COUNT(*) AS n FROM users WHERE status = 'active' AND mfa_enabled = 0
  1 row(s). [a]dd to the bank, [n]ext, [s]kip item, [d]ismiss item, [q]uit > a
  added as ex-105
```

Keys: `a` add, `n` next step, `s` skip (stays pending), `d` dismiss for good, `q` quit. For a thumbs-down, `g` asks for the correct SQL
(multi-line, ended by an empty line) and a question text, then adds an eval case.

## The checks before anything is written (`promote.py`)

Nothing is promoted automatically: a bank that any click can write to is a way to teach the model wrong answers. Each promotion is
shown to a reviewer, re-validated and re-run against the current data, and refused when:

| Refused when | Why |
|--------------|-----|
| the step used `filter_from` (`linked`) | it only makes sense together with the earlier step, not as a stand-alone example |
| the step ended with an error | not a worked example |
| SQL fails validation or scope rules, or does not run | the bank must contain only working queries |
| the question is already in the bank (or eval set) | duplicates add nothing and skew retrieval |

Bank entries are written in the exact format `rag/bank.py` parses, with the next free id and tags `feedback` plus the tool's scope.
Eval cases get ids `f-01`, `f-02`, ... and the same `indent=1` JSON style as the rest of `cases.json`, so diffs stay small. The
retrieval index notices the changed bank file and re-embeds only the new question.

## Limits

An agent answer that chains two tools can promote only its first step (the second reads the first's result). There is no review UI
yet (it is a terminal command), no un-promote, and no quality score beyond the reviewer's judgment.
