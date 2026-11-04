"""The text sent to the model: the SQL system prompt, the repair feedback and the answer prompt.

Everything here is plain strings and small functions that assemble them; nothing calls a model. The system prompt is the
role, the database schema (cut down to a tool's tables when a scope is given), the rules, and then the retrieved
examples with the best match last.
"""

from __future__ import annotations

from ..context import handoff
from ..core.db import SCHEMA_SQL
from ..guardrails.scopes import Scope
from ..rag import retrieval

TODAY = "2026-11-01"  # matches the seed data
_HEAD = "You translate questions about an identity and access governance database into one SQLite query.\n"
_COMMON_RULES = f"""- Write a single read-only SELECT statement in SQLite dialect. Use only the tables and columns above.
- Boolean columns hold 0 or 1. Dates are ISO-8601 text, so compare them as strings or with date().
- Treat today as {TODAY}. Use date('{TODAY}', '-90 days') for relative dates, never date('now').
- Select human-readable columns (names, emails) rather than only ids.
- Add only the filters the question asks for. Do not invent conditions on status, dates, entitlements or grant type.
- "How many ..." questions return one number (COUNT), not a list of rows.
- Questions about people return one row per person (GROUP BY u.id or SELECT DISTINCT), never one row per grant,
  membership or event. Put per-person counts in columns. Questions about applications return one row per application.
- "Each" and "every" list all of them; use LIMIT only for "top N", "most" or "largest" questions.
- Match a person named in the question with name LIKE '%Name%' (names are not unique), and include id and email.
"""
_IDENTITY_RULES = """- "Admin" access means entitlements.is_admin = 1. Access a user holds is in access_grants.
- Roles, jobs, positions and titles of people are users.title; entitlements are permissions inside applications.
"""
_ACTIVITY_RULES = """- Activity is in activity_events: event_type is login, read, write or admin_action; success = 0 means it failed.
  A user's last activity is MAX(ts). Users with no events at all have no rows there, so use LEFT JOIN or NOT EXISTS.
"""
_SCOPED_RULES = """- Always return the id of each person or application as the first column, named id, even when you also return names:
  another tool may use these ids as its input.
"""
_RESPOND = 'Respond with JSON: {"sql": "<the query>"}'


def system_base(scope: Scope | None = None, inputs: handoff.Input | None = None) -> str:
    """The system prompt without examples: role, schema, rules and the response format.

    Args:
        scope: Limit the schema (and the scope-specific rules) to one tool's tables; None shows the whole database.
        inputs: Values handed over from an earlier step; adds a rule describing the ``input`` table.
    """
    schema = scope.schema_sql() if scope else SCHEMA_SQL
    rules = _COMMON_RULES
    if scope is None or scope.name == "identity":
        rules += _IDENTITY_RULES
    if scope is None or scope.name == "activity":
        rules += _ACTIVITY_RULES
    if scope is not None:
        rules += _SCOPED_RULES
    if inputs is not None:
        rules += "- " + handoff.describe(inputs) + "\n"
    return f"{_HEAD}\nDatabase schema:\n{schema}\nRules:\n{rules}{_RESPOND}"


SQL_SYSTEM_BASE = system_base()  # the whole database, no hand-over: what an unscoped question uses
EXAMPLES_INTRO = """

Worked examples of similar questions with the correct query. Follow their patterns and conventions
(the closest example is last):
"""
SQL_SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
REPAIR_FEEDBACK = """That query failed validation and was not run:
{problems}

Write a corrected query that fixes every problem, and respond with JSON: {{"sql": "<the query>"}}"""
ANSWER_SYSTEM = """You answer a user's question using only the query results provided.
Be concise and concrete. Quote names and numbers exactly as they appear. List the items when there are only a few.
If a note says the results were truncated or partial, say so. Do not mention SQL unless the user asked about it.
Attach each value to the column it came from (created_at is when the account was added, terminated_at is the
termination date) and never combine or reinterpret columns. Do not guess anyone's gender: use the person's name
or "they". If several rows share a name, say so and tell them apart by email or id."""


def build_sql_system(hits: list[retrieval.Hit], scope: Scope | None = None, inputs: handoff.Input | None = None) -> str:
    """Assemble the system prompt for SQL generation.

    The rules and schema always come first. If retrieval found examples they follow, ordered so the best match is
    last (closest to the question, where small models weight it most), each with its optional note.

    Args:
        hits: Retrieved bank examples, best first. May be empty (no examples).
        scope: Restrict the schema to one tool's tables (see ``scopes.py``).
        inputs: Values handed over from an earlier step (see ``handoff.py``).
    """
    base = system_base(scope, inputs)
    if not hits:
        return base
    blocks = []
    for h in reversed(hits):  # best match last, nearest the question
        block = f"Question: {h.example.question}\nSQL: {h.example.sql}"
        if h.example.notes:
            block += f"\nNote: {h.example.notes}"
        blocks.append(block)
    return base + EXAMPLES_INTRO + "\n\n".join(blocks)


def sql_messages(
    question: str, hits: list[retrieval.Hit], scope: Scope | None = None, inputs: handoff.Input | None = None
) -> list[dict[str, str]]:
    """The chat messages for the first SQL attempt: the system prompt (with examples) and the user's question."""
    return [{"role": "system", "content": build_sql_system(hits, scope, inputs)}, {"role": "user", "content": question}]
