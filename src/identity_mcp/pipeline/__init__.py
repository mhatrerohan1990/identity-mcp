"""Question -> SQL -> rows -> natural-language answer.

Schema + retrieved bank examples in the prompt, one LLM call to write SQL, validate it (static checks, then an
EXPLAIN dry run), run it read-only, one LLM call to summarize. A query that fails validation goes back to the
model with the reasons (up to IDENTITY_MCP_MAX_REPAIRS times); if it still fails it is reported, not run.

The modules, in the order a question passes through them:

* ``engine``   - ``ask``: the whole flow for one question
* ``prompts``  - the text sent to the model
* ``sql``      - writing, checking and repairing the query
* ``executor`` - running a checked query safely
* ``answer``   - writing the plain-English answer

The names callers use are re-exported here, so ``pipeline.ask(...)`` and ``pipeline.execute(...)`` keep working.

Try it: uv run python -m identity_mcp.pipeline "How many users are terminated?"
"""

from ..guardrails.validation import ValidationError
from .answer import SUMMARY_ROWS, summarize
from .engine import ask
from .executor import MAX_ROWS, QUERY_TIMEOUT_S, QueryError, execute
from .prompts import (ANSWER_SYSTEM, EXAMPLES_INTRO, REPAIR_FEEDBACK, SQL_SCHEMA, SQL_SYSTEM_BASE, TODAY,
                      build_sql_system, sql_messages, system_base)
from .sql import DEFAULT_MAX_REPAIRS, Draft, check_sql, generate_sql, max_repairs, produce_sql, repair_sql

__all__ = [
    "ANSWER_SYSTEM", "DEFAULT_MAX_REPAIRS", "Draft", "EXAMPLES_INTRO", "MAX_ROWS", "QUERY_TIMEOUT_S", "QueryError",
    "REPAIR_FEEDBACK", "SQL_SCHEMA", "SQL_SYSTEM_BASE", "SUMMARY_ROWS", "TODAY", "ValidationError", "ask",
    "build_sql_system", "check_sql", "execute", "generate_sql", "max_repairs", "produce_sql", "repair_sql",
    "sql_messages", "summarize", "system_base",
]
