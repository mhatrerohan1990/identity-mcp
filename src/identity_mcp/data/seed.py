"""Deterministic seed data for the identity-mcp demo database.

Run: uv run python -m identity_mcp.data.seed [--db app.db]

Planted findings (counts are exact, see PLANTED below):
  * orphaned access      - terminated users who still hold grants
  * dormant admins       - active users with admin grants but no activity in 90+ days
  * privileged, no MFA   - active users in a privileged group with mfa_enabled = 0
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")
SEED = 1990
TODAY = date(2026, 11, 1)  # fixed so the dataset (and eval answers) never drift

N_USERS = 500
N_EVENTS = 20_000
N_ORPHANED = 30
N_DORMANT_ADMINS = 15
N_PRIV_NO_MFA = 20

DEPARTMENTS = {
    "Engineering": ["Software Engineer", "Senior Software Engineer", "SRE", "Engineering Manager"],
    "Sales": ["Account Executive", "Sales Engineer", "SDR", "Sales Manager"],
    "Finance": ["Accountant", "Financial Analyst", "Controller"],
    "HR": ["Recruiter", "HR Business Partner", "People Ops Specialist"],
    "IT": ["IT Support", "Systems Administrator", "Security Analyst"],
    "Marketing": ["Marketing Manager", "Content Strategist", "Designer"],
    "Legal": ["Counsel", "Paralegal", "Compliance Officer"],
}
FIRST = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Mia", "Ethan", "Lucas", "Sofia", "Aria",
         "Priya", "Arjun", "Wei", "Mei", "Hiro", "Yuki", "Omar", "Layla", "Diego", "Lucia",
         "Nina", "Ravi", "Anika", "Tomas", "Elena", "Kofi", "Amara", "Jonas", "Ingrid", "Sam"]
LAST = ["Smith", "Johnson", "Patel", "Nguyen", "Garcia", "Kim", "Chen", "Singh", "Brown", "Lopez",
        "Mhatre", "Khan", "Rossi", "Silva", "Novak", "Cohen", "Okafor", "Tanaka", "Murphy", "Dubois"]

# (name, criticality, integration, [(entitlement, is_admin), ...])
APPS = [
    ("Okta Admin Console", "high", "okta", [("Super Admin", 1), ("Read Only Admin", 0)]),
    ("Okta Dashboard", "medium", "okta", [("User", 0)]),
    ("Entra ID", "high", "entra", [("Global Administrator", 1), ("User Administrator", 1), ("Reader", 0)]),
    ("Azure Portal", "high", "entra", [("Owner", 1), ("Contributor", 0), ("Reader", 0)]),
    ("Google Workspace Admin", "high", "google", [("Super Admin", 1), ("Groups Admin", 1), ("Viewer", 0)]),
    ("Google Drive", "medium", "google", [("Editor", 0), ("Viewer", 0)]),
    ("Gmail", "medium", "google", [("User", 0)]),
    ("Salesforce CRM", "high", "salesforce", [("System Administrator", 1), ("Sales User", 0), ("Read Only", 0)]),
    ("Salesforce CPQ", "medium", "salesforce", [("Admin", 1), ("Quoter", 0)]),
    ("GitHub", "high", "okta", [("Owner", 1), ("Maintainer", 0), ("Member", 0)]),
    ("AWS Console", "high", "okta", [("AdministratorAccess", 1), ("PowerUser", 0), ("ReadOnly", 0)]),
    ("Snowflake", "high", "okta", [("ACCOUNTADMIN", 1), ("Analyst", 0), ("Viewer", 0)]),
    ("Workday", "high", "okta", [("HR Admin", 1), ("Manager", 0), ("Employee", 0)]),
    ("NetSuite", "high", "okta", [("Administrator", 1), ("Accountant", 0), ("Viewer", 0)]),
    ("Jira", "low", "okta", [("Admin", 1), ("Developer", 0), ("Viewer", 0)]),
    ("Confluence", "low", "okta", [("Admin", 1), ("Editor", 0), ("Viewer", 0)]),
    ("Slack", "low", "okta", [("Workspace Admin", 1), ("Member", 0)]),
    ("Zoom", "low", "okta", [("Admin", 1), ("Licensed", 0)]),
    ("DocuSign", "medium", "okta", [("Admin", 1), ("Sender", 0)]),
    ("Datadog", "medium", "okta", [("Admin", 1), ("Standard", 0), ("Read Only", 0)]),
    ("PagerDuty", "medium", "okta", [("Account Owner", 1), ("Responder", 0)]),
    ("Zendesk", "low", "okta", [("Admin", 1), ("Agent", 0)]),
    ("HubSpot", "low", "okta", [("Super Admin", 1), ("Marketer", 0)]),
    ("Figma", "low", "okta", [("Org Admin", 1), ("Editor", 0), ("Viewer", 0)]),
]

# (name, description, is_privileged, department the group is "about")
GROUPS = [
    ("All Employees", "Everyone in the company", 0, None),
    ("Engineering", "Engineering department", 0, "Engineering"),
    ("Sales", "Sales department", 0, "Sales"),
    ("Finance", "Finance department", 0, "Finance"),
    ("HR", "HR department", 0, "HR"),
    ("IT", "IT department", 0, "IT"),
    ("Marketing", "Marketing department", 0, "Marketing"),
    ("Legal", "Legal department", 0, "Legal"),
    ("Cloud Admins", "Admin access to AWS and Azure", 1, None),
    ("Okta Super Admins", "Full control of the identity provider", 1, None),
    ("Database Admins", "Admin access to Snowflake and RDS", 1, None),
    ("Finance Approvers", "Approve payments in NetSuite", 1, None),
]

# group name -> entitlements granted to every member: (app, entitlement)
GROUP_ENTITLEMENTS = {
    "All Employees": [("Okta Dashboard", "User"), ("Gmail", "User"), ("Slack", "Member"), ("Zoom", "Licensed"), ("Google Drive", "Viewer")],
    "Engineering": [("GitHub", "Member"), ("Jira", "Developer"), ("Confluence", "Editor"), ("Datadog", "Standard")],
    "Sales": [("Salesforce CRM", "Sales User"), ("Salesforce CPQ", "Quoter"), ("DocuSign", "Sender")],
    "Finance": [("NetSuite", "Accountant")],
    "HR": [("Workday", "Manager")],
    "IT": [("Okta Admin Console", "Read Only Admin"), ("Zendesk", "Agent")],
    "Marketing": [("HubSpot", "Marketer"), ("Figma", "Editor")],
    "Legal": [("DocuSign", "Sender")],
    "Cloud Admins": [("AWS Console", "AdministratorAccess"), ("Azure Portal", "Owner")],
    "Okta Super Admins": [("Okta Admin Console", "Super Admin")],
    "Database Admins": [("Snowflake", "ACCOUNTADMIN")],
    "Finance Approvers": [("NetSuite", "Administrator")],
}

PLANTED = {
    "orphaned_users": N_ORPHANED,
    "dormant_admins": N_DORMANT_ADMINS,
    "privileged_no_mfa": N_PRIV_NO_MFA,
}


def _d(d: date) -> str:
    return d.isoformat()


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build(db_path: Path) -> dict[str, int]:
    rng = random.Random(SEED)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA.read_text())
    con.execute("PRAGMA foreign_keys = OFF")  # users.manager_id is self-referential; verified at the end

    # ---- users -----------------------------------------------------------
    users: list[dict] = []
    seen_emails: set[str] = set()
    depts = list(DEPARTMENTS)
    weights = [30, 18, 10, 6, 10, 12, 6]  # engineering-heavy
    for uid in range(1, N_USERS + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first}.{last}@acme.example".lower()
        while email in seen_emails:  # disambiguate the email only; the display name stays clean
            email = f"{first}.{last}{rng.randint(2, 99)}@acme.example".lower()
        seen_emails.add(email)
        dept = rng.choices(depts, weights)[0]
        users.append({
            "id": uid, "email": email, "name": f"{first} {last}", "department": dept,
            "title": rng.choice(DEPARTMENTS[dept]), "manager_id": None, "status": "active",
            "mfa_enabled": 1 if rng.random() < 0.93 else 0,
            "created_at": _d(_rand_date(rng, date(2019, 1, 1), date(2025, 12, 31))),
            "terminated_at": None,
        })
    # managers: first 5% of users are managers; everyone else reports to one in-department
    managers = {d: [u for u in users[: N_USERS // 20] if u["department"] == d] for d in depts}
    for u in users:
        pool = managers[u["department"]] or users[: N_USERS // 20]
        m = rng.choice(pool)
        u["manager_id"] = None if m["id"] == u["id"] else m["id"]

    # terminated / suspended users
    ids = list(range(N_USERS // 20 + 1, N_USERS))  # never terminate managers
    rng.shuffle(ids)
    terminated = ids[: N_ORPHANED + 20]  # 30 will keep grants (orphaned), 20 are cleanly offboarded
    for i in terminated:
        u = users[i]
        u["status"] = "terminated"
        u["terminated_at"] = _d(_rand_date(rng, date(2026, 1, 1), TODAY - timedelta(days=7)))
    for i in ids[N_ORPHANED + 20: N_ORPHANED + 35]:
        users[i]["status"] = "suspended"
    orphan_ids = {users[i]["id"] for i in terminated[:N_ORPHANED]}
    clean_term_ids = {users[i]["id"] for i in terminated[N_ORPHANED:]}

    con.executemany(
        "INSERT INTO users VALUES (:id,:email,:name,:department,:title,:manager_id,:status,:mfa_enabled,:created_at,:terminated_at)",
        users,
    )
    by_id = {u["id"]: u for u in users}

    # ---- groups, apps, entitlements ---------------------------------------
    group_id = {}
    for gid, (name, desc, priv, _) in enumerate(GROUPS, 1):
        group_id[name] = gid
        con.execute("INSERT INTO groups VALUES (?,?,?,?)", (gid, name, desc, priv))

    ent_id: dict[tuple[str, str], int] = {}
    ent_admin: dict[int, int] = {}
    eid = 0
    it_users = [u["id"] for u in users if u["department"] == "IT" and u["status"] == "active"]
    for aid, (name, crit, integ, ents) in enumerate(APPS, 1):
        con.execute("INSERT INTO applications VALUES (?,?,?,?,?)", (aid, name, rng.choice(it_users), crit, integ))
        for ename, is_admin in ents:
            eid += 1
            ent_id[(name, ename)] = eid
            ent_admin[eid] = is_admin
            con.execute("INSERT INTO entitlements VALUES (?,?,?,?)", (eid, aid, ename, is_admin))
    app_names = [a[0] for a in APPS]
    app_id = {n: i for i, n in enumerate(app_names, 1)}

    # ---- group membership --------------------------------------------------
    members: dict[str, set[int]] = {g[0]: set() for g in GROUPS}
    for u in users:
        members["All Employees"].add(u["id"])
        members[u["department"]].add(u["id"])
    active = [u for u in users if u["status"] == "active"]
    eng_it = [u for u in active if u["department"] in ("Engineering", "IT")]
    fin = [u for u in active if u["department"] == "Finance"]
    for g, pool, n in (("Cloud Admins", eng_it, 14), ("Okta Super Admins", [u for u in active if u["department"] == "IT"], 8),
                       ("Database Admins", eng_it, 10), ("Finance Approvers", fin, 6)):
        members[g].update(u["id"] for u in rng.sample(pool, n))

    # planted: privileged users without MFA (active, in a privileged group)
    priv_groups = ["Cloud Admins", "Okta Super Admins", "Database Admins", "Finance Approvers"]
    candidates = [u for u in active if any(u["id"] in members[g] for g in priv_groups)]
    no_mfa = rng.sample(candidates, min(N_PRIV_NO_MFA, len(candidates)))
    for u in active:  # everyone else in a privileged group has MFA
        if any(u["id"] in members[g] for g in priv_groups):
            u["mfa_enabled"] = 1
    for u in no_mfa:
        u["mfa_enabled"] = 0
    # top up if the random privileged pool was smaller than the target
    while len(no_mfa) < N_PRIV_NO_MFA:
        u = rng.choice([x for x in active if x not in no_mfa])
        members[rng.choice(priv_groups)].add(u["id"])
        u["mfa_enabled"] = 0
        no_mfa.append(u)
    con.executemany("UPDATE users SET mfa_enabled=? WHERE id=?", [(u["mfa_enabled"], u["id"]) for u in users])

    # terminated users are removed from groups, except the orphans (who keep everything)
    for g in members:
        members[g] -= clean_term_ids

    for g, uids in members.items():
        for uid in sorted(uids):
            u = by_id[uid]
            added = max(date.fromisoformat(u["created_at"]), date(2019, 1, 1)) + timedelta(days=rng.randint(0, 30))
            con.execute("INSERT INTO group_members VALUES (?,?,?)", (group_id[g], uid, _d(added)))

    # ---- access grants -----------------------------------------------------
    grants: list[tuple] = []
    grant_keys: set[tuple[int, int]] = set()
    admin_helper = rng.choice(it_users)

    def add_grant(uid: int, ent: int, via: str, gid: int | None, when: date, by: int | None):
        if (uid, ent) in grant_keys:
            return
        grant_keys.add((uid, ent))
        grants.append((len(grants) + 1, uid, ent, via, gid, _d(when), by))

    for g, uids in members.items():
        for uid in sorted(uids):
            u = by_id[uid]
            when = date.fromisoformat(u["created_at"]) + timedelta(days=rng.randint(0, 20))
            for app, ent in GROUP_ENTITLEMENTS[g]:
                add_grant(uid, ent_id[(app, ent)], "group", group_id[g], when, None)

    # direct grants: a few extras per active user, mostly non-admin
    non_admin = [e for e, a in ent_admin.items() if not a]
    for u in users:
        if u["id"] in clean_term_ids:
            continue
        for e in rng.sample(non_admin, rng.randint(0, 3)):
            when = _rand_date(rng, date.fromisoformat(u["created_at"]), TODAY)
            add_grant(u["id"], e, "direct", None, when, rng.choice(it_users))

    # planted: dormant admins = active users holding direct admin grants, no activity for 90+ days
    admin_ents = [e for e, a in ent_admin.items() if a]
    dormant_pool = [u for u in active if u["id"] not in {x["id"] for x in no_mfa}]
    dormant = rng.sample(dormant_pool, N_DORMANT_ADMINS)
    dormant_ids = {u["id"] for u in dormant}
    for u in dormant:
        for e in rng.sample(admin_ents, rng.randint(1, 2)):
            add_grant(u["id"], e, "direct", None, _rand_date(rng, date(2023, 1, 1), date(2025, 6, 30)), admin_helper)

    # a few legitimate *active* direct admins so "who has admin" is not just the planted set
    legit_pool = [u for u in active if u["id"] not in dormant_ids and u["department"] in ("IT", "Engineering")]
    legit_admins = rng.sample(legit_pool, 25)
    legit_admin_ids = {u["id"] for u in legit_admins}
    for u in legit_admins:
        add_grant(u["id"], rng.choice(admin_ents), "direct", None, _rand_date(rng, date(2022, 1, 1), date(2026, 6, 30)), admin_helper)

    con.executemany("INSERT INTO access_grants VALUES (?,?,?,?,?,?,?)", grants)

    # ---- activity events ---------------------------------------------------
    user_apps: dict[int, list[int]] = {}
    ent_app = {e: next(a for a in range(1, len(APPS) + 1) if any(ent_id[(app_names[a - 1], n)] == e for n, _ in APPS[a - 1][3])) for e in ent_admin}
    for _, uid, ent, *_ in grants:
        user_apps.setdefault(uid, []).append(ent_app[ent])
    admin_apps = {a for e, a in ent_app.items() if ent_admin[e]}

    window_start = TODAY - timedelta(days=180)
    events: list[tuple] = []
    live = [u for u in users if u["status"] != "terminated"]
    event_types = ["login", "read", "write", "admin_action"]
    for _ in range(N_EVENTS):
        u = rng.choice(live)
        apps = user_apps.get(u["id"]) or [rng.randint(1, len(APPS))]
        a = rng.choice(apps)
        et = rng.choices(event_types, [30, 45, 20, 5 if a in admin_apps else 0])[0] if a in admin_apps else rng.choices(event_types[:3], [30, 50, 20])[0]
        last = TODAY - timedelta(days=90) if u["id"] in dormant_ids else TODAY
        if u["id"] in dormant_ids:
            last = TODAY - timedelta(days=rng.randint(91, 170))
            day = _rand_date(rng, window_start, last)
        else:
            day = _rand_date(rng, window_start, TODAY)
        ts = datetime(day.year, day.month, day.day, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59))
        ip = f"10.{rng.randint(0, 20)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}" if rng.random() < 0.9 else f"{rng.randint(11, 220)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        events.append((0, u["id"], a, et, ts.isoformat(sep=" "), ip, 1 if rng.random() < 0.96 else 0))

    # terminated users: some activity before termination only (orphans never log in after)
    for u in users:
        if u["status"] == "terminated":
            end = date.fromisoformat(u["terminated_at"])
            for _ in range(rng.randint(5, 25)):
                apps = user_apps.get(u["id"]) or [1]
                day = _rand_date(rng, end - timedelta(days=120), end)
                ts = datetime(day.year, day.month, day.day, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59))
                events.append((0, u["id"], rng.choice(apps), rng.choice(event_types[:3]), ts.isoformat(sep=" "),
                               f"10.{rng.randint(0, 20)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}", 1))
    # active admins must have recent activity so "dormant" is exactly the planted set
    for uid in legit_admin_ids:
        for app in {ent_app[e] for e in admin_ents if (uid, e) in grant_keys}:
            day = TODAY - timedelta(days=rng.randint(0, 30))
            events.append((0, uid, app, "admin_action", datetime(day.year, day.month, day.day, 10, 0, 0).isoformat(sep=" "), "10.1.1.1", 1))

    events.sort(key=lambda e: e[4])
    con.executemany("INSERT INTO activity_events VALUES (?,?,?,?,?,?,?)", [(i, *e[1:]) for i, e in enumerate(events, 1)])
    con.commit()
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    assert not bad, f"foreign key violations: {bad[:5]}"

    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("users", "groups", "group_members", "applications", "entitlements", "access_grants", "activity_events")}
    con.close()
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=Path("app.db"))
    args = p.parse_args()
    for table, n in build(args.db).items():
        print(f"{table:18} {n:>7}")
    print(f"wrote {args.db}")


if __name__ == "__main__":
    main()
