-- identity-mcp demo schema: a small identity & access governance dataset.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    department    TEXT NOT NULL,
    title         TEXT NOT NULL,
    manager_id    INTEGER REFERENCES users(id),
    status        TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'terminated')),
    mfa_enabled   INTEGER NOT NULL CHECK (mfa_enabled IN (0, 1)),
    created_at    TEXT NOT NULL,            -- ISO-8601 date
    terminated_at TEXT                      -- NULL unless status = 'terminated'
);

CREATE TABLE groups (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT NOT NULL,
    is_privileged INTEGER NOT NULL CHECK (is_privileged IN (0, 1))
);

CREATE TABLE group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id),
    user_id  INTEGER NOT NULL REFERENCES users(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE applications (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    owner_user_id INTEGER REFERENCES users(id),
    criticality   TEXT NOT NULL CHECK (criticality IN ('low', 'medium', 'high')),
    integration   TEXT NOT NULL CHECK (integration IN ('okta', 'entra', 'google', 'salesforce'))
);

CREATE TABLE entitlements (
    id       INTEGER PRIMARY KEY,
    app_id   INTEGER NOT NULL REFERENCES applications(id),
    name     TEXT NOT NULL,                 -- e.g. 'Admin', 'Editor', 'Viewer'
    is_admin INTEGER NOT NULL CHECK (is_admin IN (0, 1)),
    UNIQUE (app_id, name)
);

CREATE TABLE access_grants (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    entitlement_id INTEGER NOT NULL REFERENCES entitlements(id),
    granted_via    TEXT NOT NULL CHECK (granted_via IN ('direct', 'group')),
    group_id       INTEGER REFERENCES groups(id),   -- set iff granted_via = 'group'
    granted_at     TEXT NOT NULL,
    granted_by     INTEGER REFERENCES users(id),
    CHECK ((granted_via = 'group') = (group_id IS NOT NULL))
);

CREATE TABLE activity_events (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    app_id     INTEGER NOT NULL REFERENCES applications(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('login', 'read', 'write', 'admin_action')),
    ts         TEXT NOT NULL,               -- ISO-8601 datetime
    ip         TEXT NOT NULL,
    success    INTEGER NOT NULL CHECK (success IN (0, 1))
);

CREATE INDEX idx_grants_user    ON access_grants(user_id);
CREATE INDEX idx_grants_ent     ON access_grants(entitlement_id);
CREATE INDEX idx_members_user   ON group_members(user_id);
CREATE INDEX idx_events_user_ts ON activity_events(user_id, ts);
CREATE INDEX idx_events_app_ts  ON activity_events(app_id, ts);
