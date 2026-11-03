# GrantLens

Ask questions about identity & access data in plain English. GrantLens retrieves a few similar
curated question→SQL examples (few-shot RAG), has Claude write a query, validates it with
guardrails, runs it read-only on SQLite, and answers in natural language. Exposed as an MCP server
behind a FastAPI app that also serves a chat UI. A small-scale version of a production
governed-query assistant.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

## Status

| Step | State |
|---|---|
| Schema + deterministic seed data | done |
| Simple read-only REST API per entity | done (this README) |
| Example bank, retrieval, NL→SQL pipeline, guardrails | next |
| MCP server, chat UI, feedback, evals | planned |

## Run it

Prerequisites: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```sh
# 1. install dependencies into .venv
uv sync

# 2. create the demo database (writes ./app.db; deterministic, safe to re-run)
uv run python -m grantlens.data.seed

# 3. run the tests
uv run pytest

# 4. start the API with auto-reload
uv run uvicorn grantlens.api.app:app --reload --port 8000
```

If port 8000 is taken, pick another with `--port`. Then open:

- Interactive docs (try every endpoint in the browser): http://localhost:8000/docs
- Health check: http://localhost:8000/health

Configuration: `GRANTLENS_DB` sets the SQLite path (default `./app.db`). The API opens it
read-only. If the file is missing, endpoints return `503` with the seed command to run.

## API

No authentication or authorization yet. All endpoints are `GET` and read-only.

| Entity | Endpoints | Filters |
|---|---|---|
| users | `/users`, `/users/{id}`, `/users/{id}/grants`, `/users/{id}/groups`, `/users/{id}/activity` | `status`, `department`, `mfa_enabled`, `manager_id`, `q` (name/email substring) |
| groups | `/groups`, `/groups/{id}`, `/groups/{id}/members` | `is_privileged` |
| group memberships | `/group-members` | `group_id`, `user_id` |
| applications | `/applications`, `/applications/{id}`, `/applications/{id}/entitlements` | `criticality`, `integration`, `owner_user_id` |
| entitlements | `/entitlements`, `/entitlements/{id}` | `app_id`, `is_admin` |
| access grants | `/access-grants`, `/access-grants/{id}` | `user_id`, `entitlement_id`, `app_id`, `group_id`, `granted_via`, `is_admin` |
| activity events | `/activity-events`, `/activity-events/{id}` | `user_id`, `app_id`, `event_type`, `success`, `since`, `until` |

Every list endpoint takes `limit` (1-200, default 50) and `offset`, and returns
`{"items": [...], "total": N, "limit": L, "offset": O}`. Grants and memberships come back with
user, app, entitlement and group names joined in. Unknown ids return `404`; invalid filter values
return `422`.

### Examples

```sh
# terminated users (the orphaned-access candidates)
curl 'localhost:8000/users?status=terminated&limit=5'

# everything one user can reach
curl 'localhost:8000/users/31/grants'

# active-looking admin access granted directly, not via a group
curl 'localhost:8000/access-grants?is_admin=true&granted_via=direct'

# privileged groups and who is in them
curl 'localhost:8000/groups?is_privileged=true'
curl 'localhost:8000/groups/9/members'

# failed admin actions in October
curl 'localhost:8000/activity-events?event_type=admin_action&success=false&since=2026-10-01&until=2026-11-01'
```

## Demo data

500 users, 12 groups, 24 apps, 59 entitlements, ~4.4k grants, ~20k activity events, with planted
findings: 30 terminated users who still hold access (orphaned), 15 active admins with no activity
in 90+ days (dormant), 20 active members of privileged groups without MFA. "Today" is fixed at
2026-11-01 so results never drift.

## Layout

```
src/grantlens/data/   schema.sql, seed.py
src/grantlens/api/    app.py, routes.py, db.py
tests/                seed and API tests
docs/DESIGN.md
```
