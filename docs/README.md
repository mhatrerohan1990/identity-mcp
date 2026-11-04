# identity-mcp knowledge base

A plain-English question about an identity and access-governance dataset ("show me dormant admin accounts") goes in. A checked,
read-only SQL query runs, and a plain-English answer comes out. This folder explains how every part works, where each part
lives, and what happens, step by step, from the moment a user presses Enter.

It is written for someone who knows Python but may be new to retrieval (RAG), tool-calling agents or MCP. Each idea is explained
once, in plain words, with this project as the example.

## Read in this order

| # | File | Read it to learn |
|---|------|------------------|
| 1 | [01-overview.md](01-overview.md) | what the system is, the layers, the three ways to use it, a one-page map |
| 2 | [02-request-flow.md](02-request-flow.md) | **the walk-through**: which API is called, which guardrails run, which model calls happen, in order, with real output |
| 3 | [components/](components/) | one file per part: what it is, how it works, a small example, how it fails |
| 4 | [03-configuration.md](03-configuration.md) | every environment variable, command, port and model |
| 5 | [04-decisions-and-learnings.md](04-decisions-and-learnings.md) | why it is built this way, what was tried and dropped, measured results |
| 6 | [05-limits-and-roadmap.md](05-limits-and-roadmap.md) | what it does not do yet and what comes next |

## Components

| File | Part | One line |
|------|------|----------|
| [data.md](components/data.md) | `data/`, `core/db.py` | the demo database: seven tables, 500 users, planted findings |
| [rag.md](components/rag.md) | `rag/` | the question bank and the search that finds similar examples |
| [guardrails.md](components/guardrails.md) | `guardrails/` | validation, the read-only sandbox, scopes, value grounding |
| [pipeline.md](components/pipeline.md) | `pipeline/`, `core/llm.py` | question to SQL to rows to answer, with the repair loop |
| [context.md](components/context.md) | `context/` | follow-up questions (multi-turn) and the hand-off between tools |
| [mcp-server.md](components/mcp-server.md) | `mcp_server.py` | the two MCP tools other programs can call |
| [agent.md](components/agent.md) | `agent/` | the LangGraph loop that chains the two tools, and answer verification |
| [api-and-ui.md](components/api-and-ui.md) | `api/` | the FastAPI app, its endpoints and the chat page |
| [feedback.md](components/feedback.md) | `feedback/` | thumbs up/down and the reviewed path back into the system |
| [evals.md](components/evals.md) | `evals/`, `tests/` | how we measure quality and keep it from slipping |

## Glossary

- **LLM**: a language model. Here it runs locally through Ollama (default `qwen2.5-coder:14b`).
- **Embedding**: a list of numbers that represents the meaning of a text. Two questions with similar meaning get similar numbers.
- **RAG (retrieval-augmented generation)**: before asking the model to answer, look up relevant material and put it in the prompt.
  Here the material is *worked examples* (similar question plus the correct SQL), not documents.
- **Few-shot examples**: a handful of solved examples shown to the model so it copies their pattern.
- **Question bank**: our curated list of question-to-SQL pairs (`examples/examples.md`). It is what retrieval searches.
- **Guardrails**: code that checks the model's output before anything runs. They do not trust the model.
- **Scope**: the set of tables one MCP tool may read. Identity and activity are separate scopes.
- **MCP (Model Context Protocol)**: a standard way for a program (the *client*) to discover and call tools exposed by another
  program (the *server*). Claude Desktop is a client; our `mcp_server.py` is a server.
- **Agent / ReAct loop**: a model that repeatedly decides "call a tool" or "give the answer", sees the result, and decides again.
- **Hand-off**: passing the result of one tool call into the next ("these 72 admins: which have no activity?") without the model
  copying ids by hand.
- **Verification**: code that checks the final wording against the data it came from.
- **Eval**: a fixed set of questions with known-correct answers, run to measure quality when something changes.
