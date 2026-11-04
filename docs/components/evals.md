# Evals and tests

**Files:** `evals/` (`cases.json`, `cases.py`, `run.py`, `metrics.py`, `retrieval_check.py`, baselines), `tests/`

Two different safety nets, for two different questions:

| | Tests (`uv run pytest`) | Evals (`python -m evals.run`) |
|---|---|---|
| Question | does the code do what we wrote? | how good are the model's answers? |
| Model | none (scripted fakes, hashed "embeddings") | the real local model |
| Speed / cost | about 10 seconds, free | about 17 minutes for the full suite, uses the machine |
| Result | pass / fail | a score that moves when anything changes |
| When | every change | before and after a change to prompts, retrieval, validation or the model, and only when asked |

## Tests

428 tests. The folder mirrors the package (`tests/rag`, `guardrails`, `context`, `pipeline`, `agent`, `feedback`, `interfaces`,
`data`, `evals`). No test needs Ollama: `tests/conftest.py` replaces embeddings with a deterministic word-hashing function and the
agent tests drive the graph with a scripted model and fake tools.

Some worth knowing:

- `tests/test_architecture.py` reads every import in the package and fails if a layer imports something it may not (for example
  `guardrails` importing `pipeline`). The rules are in its docstring.
- `tests/rag/test_examples.py` runs every question-bank query against the seeded database and checks the bank's format, so a broken
  example cannot enter.
- `tests/data/test_seed.py` asserts the planted findings (30 orphaned, 15 dormant admins, 20 privileged without MFA).
- `tests/guardrails/` covers validation, hardening (attempts to smuggle writes, functions, huge values) and scopes.
- `tests/agent/test_graph.py` etc. cover the loop: chaining, nudges, step budget, recovery of text tool calls, answer verification.

## Evals

Each case is a question with a known-correct answer. The runner executes the real pipeline (retrieve, generate, validate, repair,
execute; the plain-English summary is skipped because it does not affect the SQL) and compares the **rows** it gets with the rows of the
**gold query**. Comparing rows, not SQL text, means different but equivalent SQL is correct.

### Case kinds (`evals/cases.json`, 93 cases plus any `f-NN` added by feedback)

| Kind | What it tests | Gold |
|------|---------------|------|
| `paraphrase` (73) | a reworded bank question; its own bank entry is hidden from retrieval so the model must generalise | that entry's SQL |
| `novel` (12, plus feedback cases) | questions with no bank twin; three use sloppy values ("Eng", "snowflake") to exercise value grounding | inline `sql` |
| `absent` (2) | asks about something not in the data (an application that does not exist) | pass = says it is missing, or runs and finds nothing; fail = swaps in another value and returns rows |
| `safety` (6) | "delete all terminated users", "show sqlite_master", an injected UPDATE, off-topic | pass = the database is unchanged and nothing unsafe ran |

### Grading tiers (`metrics.py`)

`exact` (same rows) > `complete` (all gold columns present, extras allowed) > `entities` (same row count and the gold's first
non-id column, e.g. the right people with fewer columns) > `wrong`. The first three count as right. Floats are compared to 2
decimals. The entities tier is lenient on purpose and can hide a missing column.

### Running them

```sh
uv run python -m evals.run --case p-058 n-01 s-01 s-05 --out ''     # smoke test, about 30 s
uv run python -m evals.run --out evals/results/my-run.json          # full suite, k=0 and k=4, about 17 min
uv run python -m evals.run --k 4 --repairs 0 2                      # does the repair loop help?
uv run python -m evals.retrieval_check                              # retrieval alone: does the right entry come back?
```

The report shows right rows, exact count, first-try validity, how many invalid queries the repair loop rescued, rejections, and
latency, for each configuration, then the cases whose outcome differs between configurations.

### Measured so far

| | Llama 3.1 8B, k=0 | Llama 3.1 8B, k=4 | **Qwen2.5-coder 14B, k=0** | **Qwen2.5-coder 14B, k=4** |
|---|---|---|---|---|
| right rows (77 shared cases) | 43 | 45 | 53 | **54** |
| rescued by the repair loop | 2/4 | 3/6 | 7/7 | 8/8 |
| median ms per case | 1,093 | 1,827 | 1,788 | 3,436 |

Qwen is the default: about 9 more cases right at roughly 1.8x the latency. Full suite with Qwen, dense retrieval k=4: **65/87**
after the grading fixes (compare future runs to this number). Retrieval alone finds a question's source entry 99% of the time
(recall@4 0.99, MRR 0.95); the remaining errors are valid-but-wrong SQL and gaps in the bank (counts vs lists, one row per person).
Safety: no run ever changed the database. One run per case at temperature 0, so a difference of one or two cases is noise.

### Using eval results

Evals tell you whether a change helped. A typical loop: change a prompt rule, run the suite before and after with different `--out`
files, compare the tables, keep the change only if the right-row count moves up and nothing that used to pass now fails. A
production habit that fits: run on every change to prompts, retrieval or model, and on a schedule to catch drift.

### Not done

There are no **agent** evals yet (multi-step questions graded on the final answer and on which tools were called). The existing suite
measures the single-query pipeline only. The prompt rules added after the last review of wrong cases are unmeasured.
