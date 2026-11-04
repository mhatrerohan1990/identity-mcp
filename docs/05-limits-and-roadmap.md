# 5. Limits and roadmap

## What it does not do today

| Area | Limit |
|------|-------|
| Security | **No authentication or authorization.** Anyone who can reach the port can call every endpoint and tool. Guardrails stop unsafe *queries*; they do not identify *callers*. |
| Chats | No saved conversations. History lives in the browser tab; refresh loses it. No conversation id on the server. |
| Agent quality | No agent evals. Only the dormant-admins chain has been checked end to end, on both local models. |
| Intent check | Verb patterns, not understanding: a rare phrasing of a write request can slip past it (it still cannot write: validation and the read-only connection hold), and a very unusual data question could be refused by mistake. |
| Answer quality | Verification checks wording against rows, not whether the query was the right one. Ambiguous names can be merged in one answer. |
| Prompt rules | Rules added after the last review of wrong cases have not been measured. |
| Bank coverage | Known gaps: count vs list phrasing, one row per person, dormant-admin counts. |
| Speed | A two-tool question takes 30 to 60 seconds on a local 14B model; the single-query summary step takes 1 to 9 s. |
| Hand-off | The result store is in-process memory: not shared across worker processes, lost on restart, 30 minute lifetime. |
| Data | SQLite and an invented dataset; no real connectors. |
| Review | Feedback is reviewed in a terminal command; no review UI. |

## Roadmap (in the order we would do it)

1. **Agent eval cases.** Multi-step questions graded on the final answer and on the tools called, run on both models. Run only on
   request. This is the measurement that makes every other agent change safe.
2. **Measure the unmeasured prompt rules** with one full eval run against the current 65/87 baseline.
3. **Fine-tuning fixes:** show email or id next to repeated names in answers; more bank entries for the failing shapes; a faster
   summary step.
4. **Server-side chat memory:** a conversation id and a LangGraph checkpointer instead of client-sent history.
5. **Authentication and authorization,** then **saved chats** (they need both).
6. **A review UI** for feedback, and a scheduled eval run to catch drift.

## Ideas parked on purpose

- Hybrid retrieval (measured, no gain).
- A second model that grades answers (code checks were chosen first; revisit if they miss too much).
- Fine-tuning the model on promoted feedback (the bank does the same job without training).
