# 3. Configuration and commands

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and the dependencies).
- [Ollama](https://ollama.com) running locally, only needed for questions (the REST data endpoints and the tests do not need it):
  `ollama pull qwen2.5-coder:14b` (about 9 GB, wants 16 GB+ RAM) and `ollama pull nomic-embed-text`. On a small machine use
  `llama3.1:8b`.

## Everyday commands

```sh
uv sync                                          # dependencies (add --extra agent for the LangGraph agent)
uv run python -m identity_mcp.data.seed          # create ./app.db (deterministic; safe to re-run)
uv run pytest                                    # all tests, no model needed
uv run uvicorn identity_mcp.api.app:app --port 8000          # chat page at /, API docs at /docs, MCP at /mcp
uv run python -m identity_mcp.mcp_server         # MCP over stdio (Claude Desktop / Code)
uv run python -m identity_mcp.pipeline "How many users are terminated?"   # one question, JSON out
uv run python -m identity_mcp.agent "Show me dormant admin accounts"      # the agent, steps on stderr
uv run python -m identity_mcp.feedback --stats                            # feedback waiting for review
uv run python -m evals.run --case p-058 n-01 --out ''                     # evals (only when you mean to)
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `IDENTITY_MCP_DB` | `./app.db` | the data file, always opened read-only |
| `IDENTITY_MCP_LLM_URL` | `http://localhost:11434` | Ollama server |
| `IDENTITY_MCP_LLM_MODEL` | `qwen2.5-coder:14b` | model for writing SQL, rewriting follow-ups and summaries |
| `IDENTITY_MCP_EMBED_MODEL` | `nomic-embed-text` | embedding model for retrieval |
| `IDENTITY_MCP_EXAMPLES_K` | `4` | examples put in the prompt; `0` turns retrieval off |
| `IDENTITY_MCP_EXAMPLES_MIN_SCORE` | `0.5` | lowest cosine similarity an example may have |
| `IDENTITY_MCP_BANK` | `examples/examples.md` | the question bank file |
| `IDENTITY_MCP_CACHE_DIR` | `.cache` | cached bank embeddings |
| `IDENTITY_MCP_MAX_REPAIRS` | `2` | extra attempts when a query fails a check; `0` disables repair |
| `IDENTITY_MCP_AGENT_MODEL` | the SQL model | model that drives the agent loop |
| `IDENTITY_MCP_AGENT_SERVER` | unset | URL of a running MCP server (for example `http://localhost:8000/mcp`); unset = launch one over stdio |
| `IDENTITY_MCP_FEEDBACK_DB` | `./feedback.db` | where thumbs up/down are stored |
| `IDENTITY_MCP_LOG_LEVEL` | `INFO` | level of the flow log |
| `IDENTITY_MCP_LOG_PROMPTS` | `1` | log full prompts and raw replies (`0` hides them) |

## Fixed values in code

| Value | Where | Meaning |
|---|---|---|
| today = 2026-11-01 | `data/seed.py`, prompts, validation | the dataset's "now" |
| 5 s / 200 rows | `pipeline/executor.py` | query timeout and row cap |
| 4000 chars | `guardrails/validation.py` | longest accepted query |
| 3 turns, 5 rows, 60 chars | `context/conversation.py` | history kept per follow-up |
| 500 values / 30 min / 50 results | `context/handoff.py` | hand-off limits |
| 25 rows | `mcp_server.py` | rows returned per tool call |
| 6 steps, 2 nudges, 1 revision | `agent/graph.py` | agent budget |

## Ports and files

- Port 8000 (change with `--port`). If something else uses it, pick another; the chat page uses relative URLs.
- Files created: `app.db` (data), `.cache/bank_embeddings.json` (embeddings), `feedback.db` (feedback), `evals/results/` (eval runs).
  All are gitignored.

## Memory and models

Ollama keeps a model loaded for a while after use. Two models are in play (the text model and the embedding model), roughly 10 to
11 GB on a 24 GB machine. `ollama ps` shows what is loaded; `ollama stop <model>` unloads one.
