"""Read-only REST endpoints, one group per entity. No authn/authz yet."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_db

DB = Annotated[sqlite3.Connection, Depends(get_db)]
Status = Literal["active", "suspended", "terminated"]
Criticality = Literal["low", "medium", "high"]
Integration = Literal["okta", "entra", "google", "salesforce"]
EventType = Literal["login", "read", "write", "admin_action"]
GrantedVia = Literal["direct", "group"]


class Page:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit, self.offset = limit, offset


P = Annotated[Page, Depends()]


def paginate(con: sqlite3.Connection, select: str, from_: str, conds: list[tuple[str, Any]], page: Page, order: str) -> dict:
    """conds is a list of (sql fragment, value); entries whose value is None are skipped.
    The value is bound once per ? in the fragment."""
    active = [(sql, v) for sql, v in conds if v is not None]
    where = " WHERE " + " AND ".join(sql for sql, _ in active) if active else ""
    params = [v for sql, v in active for _ in range(sql.count("?"))]
    total = con.execute(f"SELECT COUNT(*) FROM {from_}{where}", params).fetchone()[0]
    rows = con.execute(
        f"SELECT {select} FROM {from_}{where} ORDER BY {order} LIMIT ? OFFSET ?", [*params, page.limit, page.offset]
    ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "limit": page.limit, "offset": page.offset}


def get_one(con: sqlite3.Connection, table: str, id_: int, label: str) -> dict:
    row = con.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
    if row is None:
        raise HTTPException(404, f"{label} {id_} not found")
    return dict(row)


# Grants and members are returned with the names a caller usually wants, not just foreign keys.
GRANT_SELECT = """g.id, g.user_id, u.name AS user_name, g.entitlement_id, e.name AS entitlement_name, e.is_admin,
    e.app_id, a.name AS app_name, g.granted_via, g.group_id, g.granted_at, g.granted_by"""
GRANT_FROM = """access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
    JOIN applications a ON a.id = e.app_id"""
MEMBER_SELECT = "m.group_id, g.name AS group_name, m.user_id, u.name AS user_name, u.email, m.added_at"
MEMBER_FROM = "group_members m JOIN groups g ON g.id = m.group_id JOIN users u ON u.id = m.user_id"

# ---- users ------------------------------------------------------------------
users = APIRouter(prefix="/users", tags=["users"])


@users.get("")
def list_users(
    con: DB, page: P,
    status: Status | None = None,
    department: str | None = None,
    mfa_enabled: bool | None = None,
    manager_id: int | None = None,
    q: Annotated[str | None, Query(description="Substring match on name or email")] = None,
):
    return paginate(con, "*", "users", [
        ("status = ?", status), ("department = ?", department),
        ("mfa_enabled = ?", None if mfa_enabled is None else int(mfa_enabled)),
        ("manager_id = ?", manager_id),
        ("(name LIKE ? OR email LIKE ?)", f"%{q}%" if q else None),
    ], page, "id")


@users.get("/{user_id}")
def get_user(user_id: int, con: DB):
    return get_one(con, "users", user_id, "user")


@users.get("/{user_id}/grants")
def user_grants(user_id: int, con: DB, page: P):
    get_one(con, "users", user_id, "user")
    return paginate(con, GRANT_SELECT, GRANT_FROM, [("g.user_id = ?", user_id)], page, "g.id")


@users.get("/{user_id}/groups")
def user_groups(user_id: int, con: DB, page: P):
    get_one(con, "users", user_id, "user")
    return paginate(con, MEMBER_SELECT, MEMBER_FROM, [("m.user_id = ?", user_id)], page, "g.name")


@users.get("/{user_id}/activity")
def user_activity(user_id: int, con: DB, page: P):
    get_one(con, "users", user_id, "user")
    return paginate(con, "*", "activity_events", [("user_id = ?", user_id)], page, "ts DESC")


# ---- groups -----------------------------------------------------------------
groups = APIRouter(prefix="/groups", tags=["groups"])


@groups.get("")
def list_groups(con: DB, page: P, is_privileged: bool | None = None):
    return paginate(con, "*", "groups", [("is_privileged = ?", None if is_privileged is None else int(is_privileged))], page, "id")


@groups.get("/{group_id}")
def get_group(group_id: int, con: DB):
    return get_one(con, "groups", group_id, "group")


@groups.get("/{group_id}/members")
def group_members(group_id: int, con: DB, page: P):
    get_one(con, "groups", group_id, "group")
    return paginate(con, MEMBER_SELECT, MEMBER_FROM, [("m.group_id = ?", group_id)], page, "u.name")


# ---- group memberships ------------------------------------------------------
memberships = APIRouter(prefix="/group-members", tags=["group-members"])


@memberships.get("")
def list_memberships(con: DB, page: P, group_id: int | None = None, user_id: int | None = None):
    return paginate(con, MEMBER_SELECT, MEMBER_FROM, [("m.group_id = ?", group_id), ("m.user_id = ?", user_id)], page, "m.group_id, m.user_id")


# ---- applications -----------------------------------------------------------
applications = APIRouter(prefix="/applications", tags=["applications"])


@applications.get("")
def list_applications(
    con: DB, page: P,
    criticality: Criticality | None = None,
    integration: Integration | None = None,
    owner_user_id: int | None = None,
):
    return paginate(con, "*", "applications", [
        ("criticality = ?", criticality), ("integration = ?", integration), ("owner_user_id = ?", owner_user_id),
    ], page, "id")


@applications.get("/{app_id}")
def get_application(app_id: int, con: DB):
    return get_one(con, "applications", app_id, "application")


@applications.get("/{app_id}/entitlements")
def application_entitlements(app_id: int, con: DB, page: P):
    get_one(con, "applications", app_id, "application")
    return paginate(con, "*", "entitlements", [("app_id = ?", app_id)], page, "id")


# ---- entitlements -----------------------------------------------------------
entitlements = APIRouter(prefix="/entitlements", tags=["entitlements"])


@entitlements.get("")
def list_entitlements(con: DB, page: P, app_id: int | None = None, is_admin: bool | None = None):
    return paginate(con, "*", "entitlements", [("app_id = ?", app_id), ("is_admin = ?", None if is_admin is None else int(is_admin))], page, "id")


@entitlements.get("/{entitlement_id}")
def get_entitlement(entitlement_id: int, con: DB):
    return get_one(con, "entitlements", entitlement_id, "entitlement")


# ---- access grants ----------------------------------------------------------
grants = APIRouter(prefix="/access-grants", tags=["access-grants"])


@grants.get("")
def list_grants(
    con: DB, page: P,
    user_id: int | None = None,
    entitlement_id: int | None = None,
    app_id: int | None = None,
    group_id: int | None = None,
    granted_via: GrantedVia | None = None,
    is_admin: bool | None = None,
):
    return paginate(con, GRANT_SELECT, GRANT_FROM, [
        ("g.user_id = ?", user_id), ("g.entitlement_id = ?", entitlement_id), ("e.app_id = ?", app_id),
        ("g.group_id = ?", group_id), ("g.granted_via = ?", granted_via),
        ("e.is_admin = ?", None if is_admin is None else int(is_admin)),
    ], page, "g.id")


@grants.get("/{grant_id}")
def get_grant(grant_id: int, con: DB):
    row = con.execute(f"SELECT {GRANT_SELECT} FROM {GRANT_FROM} WHERE g.id = ?", (grant_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"access grant {grant_id} not found")
    return dict(row)


# ---- activity events --------------------------------------------------------
events = APIRouter(prefix="/activity-events", tags=["activity-events"])


@events.get("")
def list_events(
    con: DB, page: P,
    user_id: int | None = None,
    app_id: int | None = None,
    event_type: EventType | None = None,
    success: bool | None = None,
    since: Annotated[str | None, Query(description="ISO date/datetime, inclusive")] = None,
    until: Annotated[str | None, Query(description="ISO date/datetime, exclusive")] = None,
):
    return paginate(con, "*", "activity_events", [
        ("user_id = ?", user_id), ("app_id = ?", app_id), ("event_type = ?", event_type),
        ("success = ?", None if success is None else int(success)),
        ("ts >= ?", since), ("ts < ?", until),
    ], page, "ts DESC, id DESC")


@events.get("/{event_id}")
def get_event(event_id: int, con: DB):
    return get_one(con, "activity_events", event_id, "activity event")


ALL = [users, groups, memberships, applications, entitlements, grants, events]
