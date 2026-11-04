# Guardrails

**Files:** `src/identity_mcp/guardrails/validation.py`, `scopes.py`, `grounding.py`; run by `pipeline/sql.py` and `pipeline/executor.py`

## Why they exist

The model writes SQL, and a model can be wrong or be talked into something unsafe ("delete all terminated users"). So nothing the
model produces is trusted. Every query passes through code that decides whether it may run, and it runs on a connection that could
not do harm even if that code had a bug. Guardrails also protect quality: most of their messages are written so the model can read
them and fix its own query.

Two kinds of guardrail, with different jobs:

- **Safety nets** (validation, the sandbox, scopes): must never be bypassed. If they cannot decide, they reject.
- **Convenience** (value grounding): helps the model get things right; if it cannot build its catalog the query is let through
  and a warning is logged.

## Validation (`validation.py`): three layers

```
 model's SQL
   |
   v   1. STATIC     parse with sqlglot and inspect the tree; never touches the database
   |                 length, one SELECT only, allow-listed functions, known tables, scope, joins, columns
   v   2. DRY RUN    EXPLAIN on the sandbox connection: SQLite itself compiles the query without executing it
   |
   v   3. SANDBOX    the connection (used for the dry run AND the real run) has a SQLite authorizer
                     and is read-only; it holds even if layers 1-2 misread the query
```

**Static checks** (`validate_static`), in order, with the message the model gets when one fails:

| Check | Example bad query | Message |
|-------|-------------------|---------|
| too long (> 4000 chars) | | "The query is too long ... Write a simpler query." |
| syntax | `SELEC * FROM users` | "SQL syntax error: ..." |
| exactly one statement | `SELECT 1; DROP TABLE users` | "Exactly one statement is allowed, found 2." |
| only SELECT (also nested) | `DELETE FROM users` | "Only SELECT queries are allowed, got DELETE." |
| allowed functions only | `SELECT load_extension('x')` | "Function load_extension() is not allowed. Use plain aggregates and date functions such as count, sum, date, julianday, strftime." |
| no "now" | `WHERE ts > date('now')` | "Do not use 'now': today is fixed at 2026-11-01. Write date('2026-11-01', ...) instead." |
| known tables, no schema qualifiers, no table functions | `FROM sqlite_master` | "Unknown table 'sqlite_master'. Available tables: ..." |
| scope | activity tool reads `access_grants` | "Table 'access_grants' is not available in the activity tool. This tool covers: activity_events, applications, users." |
| joins have conditions | `FROM users u JOIN access_grants g` | "The join with 'g' has no join condition, so every row would be paired with every row (a cartesian product). Add ON ..." |
| columns exist | `SELECT nme FROM users` | "Column check failed: nme does not exist. Tables in this query: users(id, email, name, ...)." |

The join check exists because a join with no condition is the usual cause of queries that run until the timeout. The column check
resolves every column to its table (via sqlglot's `qualify`) and names the real columns, which is what lets a repair succeed on the
first retry.

**Dry run** (`validate_dry_run`): `EXPLAIN <query>` on the sandbox connection. Catches whatever sqlglot and SQLite disagree on.

**Sandbox** (`connect_query`): a read-only connection (`mode=ro`, `query_only`) with an *authorizer*, a callback SQLite calls for every
operation it is about to perform. This one allows only `SELECT`, reads of known tables (and only the scope's tables when scoped), and
allow-listed functions; it also limits the size of any value a query builds (stops `randomblob(1e9)`). Because it is SQLite's own
parser deciding, it holds even if a clever query fools our static analysis.

**Fail closed.** If the static analysis itself crashes, the query is rejected, not let through.

## Scopes (`scopes.py`)

A `Scope` names a tool and the tables it may read:

| Scope | Tool | Tables |
|-------|------|--------|
| `IDENTITY` | `query_identity` | users, groups, group_members, applications, entitlements, access_grants |
| `ACTIVITY` | `query_activity` | activity_events, users, applications |

A scope is enforced in **three places** so no single one has to be trusted: the *prompt* shows only the scope's tables (smaller
schema, easier for the model); *validation* rejects any other table; the *SQLite authorizer* refuses to read it even if both
earlier layers were bypassed. `tables_in(sql)` is a helper that lists the tables a query touches (also used to hide out-of-scope
bank examples). The scope also carries the tool's description text.

## Value grounding (`grounding.py`)

A query can be perfectly valid and still wrong: `WHERE department = 'Eng'` runs fine and returns nothing, because the department is
`Engineering`. Grounding catches this class of mistake.

At first use it scans the database for every small text column (department, title, status, app and group names, ...: at most 60 distinct
values, each at most 60 characters, not date-like) and keeps the real set of values. For every `col = 'x'`, `col <> 'x'` or
`col IN ('x', 'y')` in the query (columns resolved to real tables), the literal must be in that set. Otherwise the model is told:

> users.department has no value 'Eng'. Closest real values: 'Engineering'. All values: 'Engineering', 'Finance', 'HR', 'IT', 'Legal',
> 'Marketing', 'Sales'. Values are case-sensitive. If the question names something that is not in the data, keep it as is.

The last sentence matters: for a question about an application that does not exist, the right behaviour is to keep the real
(missing) value and return nothing, not to swap in a similar one. The catalog rebuilds when the database file changes.

## Where they run

`pipeline/sql.check_sql` runs: unused-input check (hand-off only) -> validation (static, dry run) -> grounding. A failure goes back to
the model for a repair (at most 2). `pipeline/executor.execute` re-runs the static checks itself before touching the database, so no
caller can execute unvalidated SQL by accident, and adds the run-time limits: **5 second timeout, 200 rows**.

## Example

```python
from identity_mcp.guardrails import validation, grounding
from identity_mcp.guardrails.scopes import ACTIVITY

validation.validate_static("DELETE FROM users")
# ['Only SELECT queries are allowed, got DELETE.']
validation.validate_static("SELECT * FROM access_grants", ACTIVITY)
# ["Table 'access_grants' is not available in the activity tool. This tool covers: ..."]
grounding.check("SELECT * FROM users WHERE department = 'Eng'")
# ["users.department has no value 'Eng'. Closest real values: 'Engineering'. ..."]
```

## What guardrails do not do

They do not judge whether a *valid, in-scope, grounded* query answers the question. A query can pass everything and still be the wrong
query; that is what evals measure (and the agent's `verify` step checks the wording against the rows, see [agent.md](agent.md)). They
also do not authenticate callers: there is no authn/authz yet.
