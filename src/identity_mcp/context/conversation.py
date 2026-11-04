"""Multi-turn context: turn a follow-up question into one that stands on its own.

"Can I get more info on him?" cannot be answered without the previous turn. Instead of making every later layer
(retrieval, SQL generation, validation) aware of conversations, this layer rewrites the follow-up *before* the
pipeline starts, using the earlier turns: "Show more info on the user with id 436 (Arjun Cohen,
arjun.cohen36@acme.example)". Everything after it is unchanged and sees an ordinary question.

The server stays stateless. The client (chat page, MCP client) sends the recent turns with each question, so
nothing is stored here and there is no session to expire.

Each earlier turn is shown to the rewriter as its question, a short answer and the first few result rows. The rows
matter: they carry ids and emails, which is how "the second one" or "the newer one" becomes a specific person even
when several people share a name.

History comes from the client, so it is treated as untrusted: it is size-limited on input (the ``Turn`` model) and
shrunk again before use (``MAX_TURNS``, ``MAX_ROWS``, ``MAX_CELL``). The rewriter only produces a *question*; whatever
SQL follows still goes through all the usual validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from ..core import llm
from ..core.logs import flow, one_line

MAX_TURNS = 3  # earlier turns shown to the rewriter (the most recent ones)
MAX_ROWS = 5  # result rows shown per earlier turn
MAX_CELL = 60  # characters kept per cell
MAX_QUESTION_CHARS = 600  # a rewrite longer than this is not trusted

REWRITE_SYSTEM = """You rewrite a user's latest message in a data-question chat into a single standalone question.

The user is asking about an identity and access governance database (users, groups, applications, entitlements,
access grants, activity events). Earlier turns are shown with their answers and the first rows of their results.

Rules:
- If the latest message depends on the earlier turns (pronouns such as he, she, they, it, them, those; phrases such
  as "the second one", "the newer one", "same for Sales", "and for terminated users", "more info"), rewrite it so
  that it is fully understandable alone. Replace each reference with the concrete thing it points to.
- When the thing is a person, application or group that appears in a result row, name it with its id and email (for
  example "the user with id 436 (Arjun Cohen, arjun.cohen36@acme.example)"), because names are not unique.
- If the latest message is already complete on its own, return it unchanged.
- Do not answer the question. Do not add conditions or details that are not in the conversation.
Respond with JSON: {"question": "<the standalone question>"}"""

REWRITE_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}


class Turn(BaseModel):
    """One earlier exchange, as sent by the client. Field limits keep a hostile or buggy client from sending a book.

    Attributes:
        question: What the user asked.
        answer: The plain-English answer that was shown (optional).
        columns: Result column names.
        rows: Result rows; only the first few are used, but a client may send up to 20.
    """

    question: str = Field(max_length=1000)
    answer: str | None = Field(default=None, max_length=4000)
    columns: list[str] = Field(default_factory=list, max_length=50)
    rows: list[list[Any]] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True)
class Rewrite:
    """Outcome of :func:`standalone_question`.

    Attributes:
        question: The question to run. Equal to the user's own when nothing needed resolving, or when rewriting failed.
        changed: True if the text differs from what the user typed.
        warning: Set when rewriting failed and the original question is used as it is.
    """

    question: str
    changed: bool = False
    warning: str | None = None


def _cell(value: Any) -> str:
    """One result cell as short text: ``NULL`` for None, and cut to ``MAX_CELL`` characters."""
    text = "NULL" if value is None else str(value)
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


def describe(history: list[Turn]) -> str:
    """The earlier turns as plain text for the rewriter prompt: the last ``MAX_TURNS``, oldest first.

    Each turn lists the question, the answer (cut to 300 characters), the column names, and at most ``MAX_ROWS`` rows.
    """
    blocks = []
    for number, turn in enumerate(history[-MAX_TURNS:], start=1):
        lines = [f"Turn {number}", f"User: {turn.question}"]
        if turn.answer:
            lines.append(f"Answer: {one_line(turn.answer, 300)}")
        if turn.columns and turn.rows:
            lines.append("Result columns: " + ", ".join(turn.columns))
            shown = turn.rows[:MAX_ROWS]
            lines += ["  " + " | ".join(_cell(v) for v in row) for row in shown]
            if len(turn.rows) > len(shown):
                lines.append(f"  ... {len(turn.rows) - len(shown)} more row(s) not shown")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def standalone_question(question: str, history: list[Turn] | None) -> Rewrite:
    """Make `question` understandable without `history`.

    With no history nothing is sent to the model, so a first question costs nothing extra. Otherwise one model call
    rewrites it. If that call fails, or returns something empty or implausibly long, the user's own question is used
    unchanged and ``warning`` says so: a missed rewrite degrades to the old single-turn behaviour instead of failing.
    """
    if not history:
        return Rewrite(question)
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": f"Earlier turns:\n\n{describe(history)}\n\nLatest message: {question}"},
    ]
    try:
        reply = json.loads(await llm.chat(messages, schema=REWRITE_SCHEMA))["question"].strip()
    except (llm.LLMError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as e:
        flow.warning("follow-up rewrite failed, using the question as typed: %s", e)
        return Rewrite(question, warning=f"Could not use the conversation so far ({e}); answered the question as typed.")
    if not reply or len(reply) > MAX_QUESTION_CHARS:
        flow.warning("follow-up rewrite unusable (%d characters), using the question as typed", len(reply))
        return Rewrite(question, warning="Could not use the conversation so far; answered the question as typed.")
    changed = " ".join(reply.split()) != " ".join(question.split())
    flow.info("follow-up rewritten: %r -> %r", one_line(question, 120), one_line(reply, 240))
    return Rewrite(reply, changed)
