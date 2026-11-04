"""Scopes: which tables each MCP tool may see.

The server exposes two tools instead of one general one, mirroring a deployment where the data live in different stores
(identity data in a relational database, activity in a search index). Each tool is the same question-to-SQL pipeline
restricted to a *scope*, a fixed set of tables:

* ``identity`` - users, groups, group memberships, applications, entitlements and access grants: who exists and who
  can access what.
* ``activity`` - activity events, plus ``users`` and ``applications`` so events can be shown with names: who did what,
  when, and from where.

A scope is enforced in three places so that no one of them has to be trusted alone:

1. the **prompt** shows only the scope's tables (a smaller schema is also easier for the model);
2. **validation** rejects any query that names a table outside the scope (``validation.validate_static``);
3. the **SQLite sandbox** refuses to read such a table even if the first two were bypassed
   (``validation.connect_query``).

Questions that need both sides ("dormant admins": admin grants plus recent activity) cannot be answered by one scoped
tool. They are answered by a client that calls both tools in turn and hands the first result to the second (see
``handoff.py``), exactly as the two stores would have to be combined in production.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.db import SCHEMA_SQL
from .validation import SCHEMA


def tables_in(sql: str) -> frozenset[str]:
    """The schema tables a query mentions, found by whole-word match against the table names.

    This is a text match, not a parse, so it is meant for sorting queries into scopes (for example bank examples), never
    for enforcement; enforcement uses the parsed query in ``validation.py``.
    """
    return frozenset(t for t in SCHEMA if re.search(rf"\b{re.escape(t)}\b", sql, re.IGNORECASE))


@dataclass(frozen=True)
class Scope:
    """A set of tables served by one MCP tool.

    Attributes:
        name: Short name, e.g. ``identity``; also the value reported in tool results.
        tool: The MCP tool name, e.g. ``query_identity``.
        tables: Every table a query in this scope may read.
        description: What the scope covers, written for the language model that has to choose between tools.
    """

    name: str
    tool: str
    tables: frozenset[str]
    description: str

    def allows(self, sql: str) -> bool:
        """True if every table `sql` mentions is in this scope. Used to pick bank examples that fit."""
        return tables_in(sql) <= self.tables

    def schema_sql(self) -> str:
        """The schema DDL for this scope's tables only (with its comments and indexes), for the SQL prompt."""
        kept = []
        for block in SCHEMA_SQL.split("\n\n"):
            table = re.search(r"CREATE TABLE (\w+)", block)
            if table:
                if table.group(1) in self.tables:
                    kept.append(block)
                continue
            indexes = [
                line for line in block.splitlines()
                if (m := re.search(r"CREATE INDEX \w+\s+ON (\w+)", line)) and m.group(1) in self.tables
            ]
            if indexes:
                kept.append("\n".join(indexes))
        return "\n\n".join(kept) + "\n"


IDENTITY = Scope(
    name="identity",
    tool="query_identity",
    tables=frozenset({"users", "groups", "group_members", "applications", "entitlements", "access_grants"}),
    description=(
        "People, groups and access: users (department, title, manager, status, MFA), group membership, applications and "
        "their owners and criticality, entitlements (which are admin), and who holds which access, directly or through a "
        "group. Cannot see activity events (logins, reads, writes, admin actions)."
    ),
)

ACTIVITY = Scope(
    name="activity",
    tool="query_activity",
    tables=frozenset({"activity_events", "users", "applications"}),
    description=(
        "What users did: activity events (login, read, write, admin_action) with time, application, IP address and "
        "success, shown with user and application names. Cannot see groups, entitlements or access grants."
    ),
)

SCOPES = {s.name: s for s in (IDENTITY, ACTIVITY)}
