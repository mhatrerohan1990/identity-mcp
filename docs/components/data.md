# Data: the demo database

**Files:** `src/identity_mcp/data/schema.sql`, `data/seed.py`, `core/db.py`

## What it is

A small, invented identity-and-access dataset in SQLite, standing in for the real stores of a production system. It is built by a
deterministic script (`random.seed(1990)`), so every run produces the same 500 users and the same answers. That is what makes
tests and evals possible: the right answer to "how many users are terminated?" is always 50.

"Today" is fixed at **2026-11-01** (`seed.TODAY`, repeated in the prompts and validation). Without that, "no activity in the last 90
days" would change answers every day.

## The seven tables

| Table | Rows | What a row is | Key columns |
|-------|------|---------------|-------------|
| `users` | 500 | a person | `id`, `email`, `name`, `department`, `title`, `manager_id`, `status` (active/suspended/terminated), `mfa_enabled` (0/1), `created_at`, `terminated_at` |
| `groups` | few | a group | `id`, `name`, `description`, `is_privileged` |
| `group_members` | | who is in which group | `group_id`, `user_id`, `added_at` |
| `applications` | | an app | `id`, `name`, `owner_user_id`, `criticality` (low/medium/high), `integration` (okta/entra/google/salesforce) |
| `entitlements` | | a permission inside an app | `id`, `app_id`, `name`, `is_admin` (0/1) |
| `access_grants` | | a person holds a permission | `user_id`, `entitlement_id`, `granted_via` (direct/group), `group_id`, `granted_at`, `granted_by` |
| `activity_events` | ~20,000 | something a person did | `user_id`, `app_id`, `event_type` (login/read/write/admin_action), `ts`, `ip`, `success` |

Access reaches a person two ways: **directly** (a row in `access_grants` with `granted_via = 'direct'`) or **through a group**
(`granted_via = 'group'`, with `group_id`). Questions about "who has access" count both unless the question names one.

## Planted findings

The seed deliberately creates three situations a governance tool exists to find. Their sizes are constants in `seed.PLANTED`, and
tests assert them:

| Finding | Meaning | Count |
|---------|---------|-------|
| orphaned access | terminated users who still hold grants | 30 |
| dormant admins | active users with admin grants and no activity in 90+ days | 15 |
| privileged without MFA | active members of a privileged group with MFA off | 20 |

## Names repeat on purpose

Names come from 30 first names x 20 last names for 500 people, so duplicates are certain ("Ava Khan" appears twice). That is realistic
and it matters: never identify a person by name alone. Match with `name LIKE '%Name%'` and return `id` and `email`. The prompts, the
hand-off (only `id`, `*_id` and `email` columns may be used to pass results) and the answer prompt all reflect this.

## Database access rules (`core/db.py`)

`connect_ro()` opens the file with `mode=ro` and `PRAGMA query_only = ON`: no code using it can modify data. The path is
`$IDENTITY_MCP_DB` or `app.db` in the working directory. The application writes only to a *different* file (`feedback.db`).

## Example

```sh
uv run python -m identity_mcp.data.seed            # writes ./app.db (safe to re-run)
sqlite3 app.db "SELECT status, COUNT(*) FROM users GROUP BY status"
```

## How it fails

If `app.db` is missing, `connect_ro` raises `FileNotFoundError` with the command above; the API turns this into a 503 and the
pipeline into a readable answer.
