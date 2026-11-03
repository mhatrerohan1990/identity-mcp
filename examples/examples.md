# Question bank

100 curated English questions, each with the SQLite query we expect to be generated for it.
Source of truth for the future few-shot retrieval step; nothing reads this file yet.

Conventions the queries follow (and that a generator should learn):
- SQLite dialect, one read-only `SELECT`. Schema: `src/identity_mcp/data/schema.sql`.
- "Today" is **2026-11-01**. Relative dates are written `date('2026-11-01', '-90 days')`, never `date('now')`.
- Booleans are 0/1. "Admin" means `entitlements.is_admin = 1`. "Active" means `users.status = 'active'`.
- Questions about people return one row per user: use `EXISTS` / `GROUP BY` rather than a join that repeats a row per grant.
- Access counts BOTH direct and group-inherited grants unless the question names one (`granted_via`).
- Return readable columns (names, emails), not just ids.
- Tag `empty-ok` marks questions whose correct answer in the demo data is zero rows.

Entry format (parsed by `tests/test_examples.py`): `## ex-NNN`, `**Q:**`, `**Tags:**`, a ```sql block, optional `**Notes:**`.
Every query is executed against the seeded demo database by the test suite.


## ex-001
**Q:** How many users are there?
**Tags:** users, count

```sql
SELECT COUNT(*) AS user_count FROM users
```

## ex-002
**Q:** How many active users do we have?
**Tags:** users, count, status

```sql
SELECT COUNT(*) AS active_users FROM users WHERE status = 'active'
```

## ex-003
**Q:** How many users are terminated?
**Tags:** users, count, status, terminated

```sql
SELECT COUNT(*) AS terminated_users FROM users WHERE status = 'terminated'
```

## ex-004
**Q:** How many users are currently suspended?
**Tags:** users, count, status

```sql
SELECT COUNT(*) AS suspended_users FROM users WHERE status = 'suspended'
```

## ex-005
**Q:** How many users are in each status?
**Tags:** users, group-by, status

```sql
SELECT status, COUNT(*) AS user_count FROM users GROUP BY status ORDER BY user_count DESC
```

## ex-006
**Q:** How many users are in each department?
**Tags:** users, group-by, department

```sql
SELECT department, COUNT(*) AS user_count FROM users GROUP BY department ORDER BY user_count DESC
```

## ex-007
**Q:** Can you show me all departments in my tenant?
**Tags:** users, department

```sql
SELECT DISTINCT department FROM users ORDER BY department
```

## ex-008
**Q:** How many applications do we have?
**Tags:** applications, count

```sql
SELECT COUNT(*) AS application_count FROM applications
```

## ex-009
**Q:** How many groups are there?
**Tags:** groups, count

```sql
SELECT COUNT(*) AS group_count FROM groups
```

## ex-010
**Q:** How many access grants exist in total?
**Tags:** access_grants, count

```sql
SELECT COUNT(*) AS grant_count FROM access_grants
```

## ex-011
**Q:** List all active users in the Engineering department.
**Tags:** users, filter, department

```sql
SELECT name, email, title FROM users WHERE department = 'Engineering' AND status = 'active' ORDER BY name
```

## ex-012
**Q:** Which active users do not have MFA enabled?
**Tags:** users, mfa, filter

```sql
SELECT name, email, department FROM users WHERE status = 'active' AND mfa_enabled = 0 ORDER BY department, name
```

**Notes:** Only active users: terminated and suspended accounts cannot sign in, so their MFA state is not a live risk.

## ex-013
**Q:** What percentage of active users have MFA enabled?
**Tags:** users, mfa, percentage

```sql
SELECT ROUND(100.0 * SUM(mfa_enabled) / COUNT(*), 1) AS mfa_pct FROM users WHERE status = 'active'
```

## ex-014
**Q:** Show MFA adoption by department for active users.
**Tags:** users, mfa, department, percentage

```sql
SELECT department, COUNT(*) AS active_users, SUM(mfa_enabled) AS with_mfa,
       ROUND(100.0 * SUM(mfa_enabled) / COUNT(*), 1) AS mfa_pct
FROM users WHERE status = 'active'
GROUP BY department ORDER BY mfa_pct, department
```

## ex-015
**Q:** Find users whose name contains 'Patel'.
**Tags:** users, search

```sql
SELECT name, email, department, title, status FROM users WHERE name LIKE '%Patel%' ORDER BY name
```

## ex-016
**Q:** Which users were created in the last 12 months?
**Tags:** users, recent, created

```sql
SELECT name, email, department, created_at FROM users WHERE created_at >= date('2026-11-01', '-12 months') ORDER BY created_at DESC
```

**Notes:** Relative dates use the fixed 'today' of 2026-11-01, never date('now').

## ex-017
**Q:** List everyone with the title Security Analyst.
**Tags:** users, title

```sql
SELECT name, email, department, status FROM users WHERE title = 'Security Analyst' ORDER BY name
```

## ex-018
**Q:** What job titles exist in Finance and how many people hold each?
**Tags:** users, title, group-by, department

```sql
SELECT title, COUNT(*) AS people FROM users WHERE department = 'Finance' GROUP BY title ORDER BY people DESC
```

## ex-019
**Q:** List terminated users with their termination dates, most recent first.
**Tags:** users, terminated, dates

```sql
SELECT name, email, department, terminated_at FROM users WHERE status = 'terminated' ORDER BY terminated_at DESC
```

## ex-020
**Q:** Which users were terminated in the last 90 days?
**Tags:** users, terminated, recent

```sql
SELECT name, email, department, terminated_at FROM users WHERE status = 'terminated' AND terminated_at >= date('2026-11-01', '-90 days') ORDER BY terminated_at DESC
```

## ex-021
**Q:** Who are the managers and how many direct reports does each have?
**Tags:** users, managers, group-by

```sql
SELECT m.name AS manager, m.department, COUNT(*) AS direct_reports
FROM users u JOIN users m ON m.id = u.manager_id
GROUP BY m.id ORDER BY direct_reports DESC, manager
```

## ex-022
**Q:** List everyone who reports directly to user 5.
**Tags:** users, managers

```sql
SELECT id, name, email, title, status FROM users WHERE manager_id = 5 ORDER BY name
```

## ex-023
**Q:** Which active users have no manager assigned?
**Tags:** users, managers, null

```sql
SELECT name, email, department, title FROM users WHERE status = 'active' AND manager_id IS NULL ORDER BY name
```

## ex-024
**Q:** Which managers have terminated users in their team?
**Tags:** users, managers, terminated

```sql
SELECT m.name AS manager, m.department, COUNT(*) AS terminated_reports
FROM users u JOIN users m ON m.id = u.manager_id
WHERE u.status = 'terminated'
GROUP BY m.id ORDER BY terminated_reports DESC, manager
```

## ex-025
**Q:** Show each Legal user together with their manager's name.
**Tags:** users, managers, join

```sql
SELECT u.name AS employee, u.title, m.name AS manager
FROM users u LEFT JOIN users m ON m.id = u.manager_id
WHERE u.department = 'Legal' ORDER BY u.name
```

**Notes:** LEFT JOIN so users without a manager still appear.

## ex-026
**Q:** List all groups with their member counts.
**Tags:** groups, group-by, members

```sql
SELECT g.name, g.is_privileged, COUNT(gm.user_id) AS members
FROM groups g LEFT JOIN group_members gm ON gm.group_id = g.id
GROUP BY g.id ORDER BY members DESC
```

## ex-027
**Q:** Which groups are privileged?
**Tags:** groups, privileged

```sql
SELECT name, description FROM groups WHERE is_privileged = 1 ORDER BY name
```

## ex-028
**Q:** Who is in the Cloud Admins group?
**Tags:** groups, members, join

```sql
SELECT u.name, u.email, u.department, u.status
FROM group_members gm JOIN groups g ON g.id = gm.group_id JOIN users u ON u.id = gm.user_id
WHERE g.name = 'Cloud Admins' ORDER BY u.name
```

## ex-029
**Q:** How many members does each privileged group have?
**Tags:** groups, privileged, count

```sql
SELECT g.name, COUNT(gm.user_id) AS members
FROM groups g LEFT JOIN group_members gm ON gm.group_id = g.id
WHERE g.is_privileged = 1 GROUP BY g.id ORDER BY members DESC
```

## ex-030
**Q:** Which users belong to more than one privileged group?
**Tags:** groups, privileged, having

```sql
SELECT u.name, u.email, GROUP_CONCAT(g.name, ', ') AS privileged_groups
FROM users u JOIN group_members gm ON gm.user_id = u.id JOIN groups g ON g.id = gm.group_id
WHERE g.is_privileged = 1
GROUP BY u.id HAVING COUNT(DISTINCT g.id) > 1 ORDER BY u.name
```

## ex-031
**Q:** Which groups have no members?
**Tags:** groups, empty-ok, anti-join

```sql
SELECT g.name FROM groups g
LEFT JOIN group_members gm ON gm.group_id = g.id
GROUP BY g.id HAVING COUNT(gm.user_id) = 0
```

**Notes:** Expected to return nothing in the demo data; useful as an empty-result example.

## ex-032
**Q:** Which terminated users are still members of a group?
**Tags:** groups, terminated, orphaned

```sql
SELECT u.name, u.email, u.terminated_at, GROUP_CONCAT(g.name, ', ') AS groups
FROM users u JOIN group_members gm ON gm.user_id = u.id JOIN groups g ON g.id = gm.group_id
WHERE u.status = 'terminated'
GROUP BY u.id ORDER BY u.terminated_at DESC
```

## ex-033
**Q:** Which active users are not in the group for their own department?
**Tags:** groups, department, empty-ok, anti-join

```sql
SELECT u.name, u.email, u.department
FROM users u JOIN groups g ON g.name = u.department
WHERE u.status = 'active'
  AND NOT EXISTS (SELECT 1 FROM group_members gm WHERE gm.group_id = g.id AND gm.user_id = u.id)
ORDER BY u.department, u.name
```

**Notes:** Department groups share the department's name. Expected to be empty in the demo data (a data-quality check).

## ex-034
**Q:** List all applications with their criticality and owner.
**Tags:** applications, owner, join

```sql
SELECT a.name AS application, a.criticality, a.integration, o.name AS owner
FROM applications a LEFT JOIN users o ON o.id = a.owner_user_id ORDER BY a.name
```

## ex-035
**Q:** Which applications are high criticality?
**Tags:** applications, criticality

```sql
SELECT name, integration FROM applications WHERE criticality = 'high' ORDER BY name
```

## ex-036
**Q:** How many applications use each integration?
**Tags:** applications, integration, group-by

```sql
SELECT integration, COUNT(*) AS applications FROM applications GROUP BY integration ORDER BY applications DESC
```

## ex-037
**Q:** How many entitlements does each application have?
**Tags:** entitlements, applications, group-by

```sql
SELECT a.name AS application, COUNT(e.id) AS entitlements
FROM applications a LEFT JOIN entitlements e ON e.app_id = a.id
GROUP BY a.id ORDER BY entitlements DESC, a.name
```

## ex-038
**Q:** Which entitlements grant admin rights?
**Tags:** entitlements, admin

```sql
SELECT a.name AS application, e.name AS entitlement
FROM entitlements e JOIN applications a ON a.id = e.app_id
WHERE e.is_admin = 1 ORDER BY a.name, e.name
```

## ex-039
**Q:** Which applications have more than one admin entitlement?
**Tags:** entitlements, admin, having

```sql
SELECT a.name AS application, COUNT(*) AS admin_entitlements
FROM entitlements e JOIN applications a ON a.id = e.app_id
WHERE e.is_admin = 1 GROUP BY a.id HAVING COUNT(*) > 1 ORDER BY admin_entitlements DESC, a.name
```

## ex-040
**Q:** Who owns the most applications?
**Tags:** applications, owner, group-by

```sql
SELECT o.name AS owner, COUNT(*) AS applications
FROM applications a JOIN users o ON o.id = a.owner_user_id
GROUP BY o.id ORDER BY applications DESC, owner LIMIT 10
```

## ex-041
**Q:** Are any applications owned by someone who is no longer active?
**Tags:** applications, owner, empty-ok

```sql
SELECT a.name AS application, o.name AS owner, o.status AS owner_status
FROM applications a JOIN users o ON o.id = a.owner_user_id
WHERE o.status <> 'active' ORDER BY a.name
```

**Notes:** Expected to be empty in the demo data.

## ex-042
**Q:** List every entitlement for the AWS Console application.
**Tags:** entitlements, applications

```sql
SELECT e.name AS entitlement, e.is_admin
FROM entitlements e JOIN applications a ON a.id = e.app_id
WHERE a.name = 'AWS Console' ORDER BY e.name
```

## ex-043
**Q:** Which applications have no admin entitlement defined?
**Tags:** applications, entitlements, anti-join

```sql
SELECT a.name FROM applications a
WHERE NOT EXISTS (SELECT 1 FROM entitlements e WHERE e.app_id = a.id AND e.is_admin = 1)
ORDER BY a.name
```

## ex-044
**Q:** How many access grants does each application have?
**Tags:** access_grants, applications, group-by

```sql
SELECT a.name AS application, COUNT(*) AS grants
FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id JOIN applications a ON a.id = e.app_id
GROUP BY a.id ORDER BY grants DESC LIMIT 15
```

## ex-045
**Q:** What are the 10 most widely granted entitlements?
**Tags:** access_grants, entitlements, top-n

```sql
SELECT a.name AS application, e.name AS entitlement, COUNT(*) AS holders
FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id JOIN applications a ON a.id = e.app_id
GROUP BY e.id ORDER BY holders DESC LIMIT 10
```

## ex-046
**Q:** How many access grants are direct and how many come from a group?
**Tags:** access_grants, direct, group, group-by

```sql
SELECT granted_via, COUNT(*) AS grants FROM access_grants GROUP BY granted_via
```

## ex-047
**Q:** Which users hold the most access grants?
**Tags:** access_grants, users, top-n

```sql
SELECT u.name, u.email, COUNT(*) AS grants
FROM access_grants g JOIN users u ON u.id = g.user_id
GROUP BY u.id ORDER BY grants DESC, u.name LIMIT 10
```

## ex-048
**Q:** What access does user 100 have?
**Tags:** access_grants, user, join

```sql
SELECT a.name AS application, e.name AS entitlement, e.is_admin, g.granted_via, grp.name AS via_group
FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id JOIN applications a ON a.id = e.app_id
LEFT JOIN groups grp ON grp.id = g.group_id
WHERE g.user_id = 100 ORDER BY a.name, e.name
```

## ex-049
**Q:** Which users have admin access granted directly rather than through a group?
**Tags:** access_grants, admin, direct

```sql
SELECT u.name, u.email, a.name AS application, e.name AS entitlement, g.granted_at
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE e.is_admin = 1 AND g.granted_via = 'direct' ORDER BY u.name
```

## ex-050
**Q:** Which active users have admin access to more than one application?
**Tags:** access_grants, admin, having

```sql
SELECT u.name, u.email, COUNT(DISTINCT e.app_id) AS admin_apps
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
WHERE e.is_admin = 1 AND u.status = 'active'
GROUP BY u.id HAVING COUNT(DISTINCT e.app_id) > 1 ORDER BY admin_apps DESC, u.name
```

**Notes:** Counts admin access however it was granted (direct or via a group).

## ex-051
**Q:** Who has Super Admin on the Okta Admin Console?
**Tags:** access_grants, specific-app, admin

```sql
SELECT u.name, u.email, u.status, g.granted_via
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE a.name = 'Okta Admin Console' AND e.name = 'Super Admin' ORDER BY u.name
```

## ex-052
**Q:** Which users have access to Snowflake?
**Tags:** access_grants, specific-app

```sql
SELECT DISTINCT u.name, u.email, u.status
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE a.name = 'Snowflake' ORDER BY u.name
```

## ex-053
**Q:** Who has granted the most direct access?
**Tags:** access_grants, granted_by, direct

```sql
SELECT gb.name AS granted_by, COUNT(*) AS direct_grants
FROM access_grants g JOIN users gb ON gb.id = g.granted_by
WHERE g.granted_via = 'direct' GROUP BY gb.id ORDER BY direct_grants DESC LIMIT 10
```

## ex-054
**Q:** Which access comes from the Cloud Admins group?
**Tags:** access_grants, groups

```sql
SELECT a.name AS application, e.name AS entitlement, COUNT(*) AS grants
FROM access_grants g JOIN groups grp ON grp.id = g.group_id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE grp.name = 'Cloud Admins' GROUP BY e.id ORDER BY a.name
```

## ex-055
**Q:** Which users have access to both Salesforce CRM and NetSuite?
**Tags:** access_grants, multi-app, having

```sql
SELECT u.name, u.email
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE a.name IN ('Salesforce CRM', 'NetSuite')
GROUP BY u.id HAVING COUNT(DISTINCT a.name) = 2 ORDER BY u.name
```

**Notes:** HAVING COUNT(DISTINCT ...) = N is the pattern for 'has all of these'.

## ex-056
**Q:** Which active users have access to the most distinct applications?
**Tags:** access_grants, applications, top-n

```sql
SELECT u.name, u.email, COUNT(DISTINCT e.app_id) AS applications
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
WHERE u.status = 'active' GROUP BY u.id ORDER BY applications DESC, u.name LIMIT 10
```

## ex-057
**Q:** Which users have both direct and group-based grants?
**Tags:** access_grants, direct, group, having

```sql
SELECT u.name, u.email, SUM(g.granted_via = 'direct') AS direct_grants, SUM(g.granted_via = 'group') AS group_grants
FROM access_grants g JOIN users u ON u.id = g.user_id
GROUP BY u.id HAVING COUNT(DISTINCT g.granted_via) = 2 ORDER BY direct_grants DESC, u.name LIMIT 25
```

## ex-058
**Q:** Which terminated users still have access?
**Tags:** orphaned, terminated, access_grants

```sql
SELECT u.name, u.email, u.terminated_at, COUNT(g.id) AS remaining_grants
FROM users u JOIN access_grants g ON g.user_id = u.id
WHERE u.status = 'terminated'
GROUP BY u.id ORDER BY remaining_grants DESC, u.name
```

**Notes:** Orphaned access. Count BOTH direct and group-inherited grants: do not filter on granted_via. One row per user.

## ex-059
**Q:** How many terminated users still have access to anything?
**Tags:** orphaned, terminated, count

```sql
SELECT COUNT(DISTINCT u.id) AS users_with_orphaned_access
FROM users u JOIN access_grants g ON g.user_id = u.id
WHERE u.status = 'terminated'
```

## ex-060
**Q:** Which terminated users still have admin access?
**Tags:** orphaned, terminated, admin, empty-ok

```sql
SELECT DISTINCT u.name, u.email, u.terminated_at, a.name AS application, e.name AS entitlement
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'terminated' AND e.is_admin = 1 ORDER BY u.terminated_at DESC, u.name
```

**Notes:** Expected to be empty in the demo data: leftover access is non-admin.

## ex-061
**Q:** Which terminated users still have access to high-criticality applications?
**Tags:** orphaned, terminated, criticality

```sql
SELECT u.name, u.email, a.name AS application, e.name AS entitlement
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'terminated' AND a.criticality = 'high' ORDER BY u.name, a.name
```

## ex-062
**Q:** Which applications do terminated users still have access to?
**Tags:** orphaned, terminated, applications

```sql
SELECT a.name AS application, COUNT(DISTINCT u.id) AS terminated_users
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'terminated' GROUP BY a.id ORDER BY terminated_users DESC, a.name
```

## ex-063
**Q:** How many days ago was each user with leftover access terminated?
**Tags:** orphaned, terminated, dates

```sql
SELECT u.name, u.email, u.terminated_at,
       CAST(julianday('2026-11-01') - julianday(u.terminated_at) AS INTEGER) AS days_since_termination,
       COUNT(g.id) AS remaining_grants
FROM users u JOIN access_grants g ON g.user_id = u.id
WHERE u.status = 'terminated'
GROUP BY u.id ORDER BY days_since_termination DESC
```

## ex-064
**Q:** Did any terminated user have activity after their termination date?
**Tags:** orphaned, terminated, activity, empty-ok

```sql
SELECT u.name, u.email, u.terminated_at, ev.ts, a.name AS application, ev.event_type
FROM users u JOIN activity_events ev ON ev.user_id = u.id JOIN applications a ON a.id = ev.app_id
WHERE u.status = 'terminated' AND date(ev.ts) > u.terminated_at ORDER BY ev.ts DESC
```

**Notes:** Expected to be empty in the demo data: orphaned users keep access but never use it. terminated_at is a date and ts a datetime, so compare date(ev.ts), not ev.ts, or same-day events count as 'after'.

## ex-065
**Q:** Which departments have the most terminated users with leftover access?
**Tags:** orphaned, terminated, department, group-by

```sql
SELECT u.department, COUNT(DISTINCT u.id) AS users_with_leftover_access
FROM users u JOIN access_grants g ON g.user_id = u.id
WHERE u.status = 'terminated' GROUP BY u.department ORDER BY users_with_leftover_access DESC
```

## ex-066
**Q:** Which terminated users have no remaining access at all?
**Tags:** orphaned, terminated, clean

```sql
SELECT u.name, u.email, u.terminated_at FROM users u
WHERE u.status = 'terminated' AND NOT EXISTS (SELECT 1 FROM access_grants g WHERE g.user_id = u.id)
ORDER BY u.terminated_at DESC
```

**Notes:** The cleanly offboarded users; the anti-join counterpart of the orphaned-access questions.

## ex-067
**Q:** Which active users have had no activity in the last 90 days?
**Tags:** dormant, activity, anti-join

```sql
SELECT u.name, u.email, u.department,
       (SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id) AS last_activity
FROM users u
WHERE u.status = 'active'
  AND NOT EXISTS (SELECT 1 FROM activity_events a WHERE a.user_id = u.id AND a.ts >= date('2026-11-01', '-90 days'))
ORDER BY last_activity
```

## ex-068
**Q:** Which active admins have had no activity in the last 90 days?
**Tags:** dormant, admin, activity

```sql
SELECT u.name, u.email, u.department,
       (SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id) AS last_activity
FROM users u
WHERE u.status = 'active'
  AND EXISTS (SELECT 1 FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id
              WHERE g.user_id = u.id AND e.is_admin = 1)
  AND COALESCE((SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id), '') < date('2026-11-01', '-90 days')
ORDER BY last_activity
```

**Notes:** Dormant admins. One row per user: use EXISTS for the admin check, not a join that repeats rows per grant. Compare each user's overall last activity, not their grant dates.

## ex-069
**Q:** How many dormant admins do we have?
**Tags:** dormant, admin, count

```sql
SELECT COUNT(*) AS dormant_admins
FROM users u
WHERE u.status = 'active'
  AND EXISTS (SELECT 1 FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id
              WHERE g.user_id = u.id AND e.is_admin = 1)
  AND COALESCE((SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id), '') < date('2026-11-01', '-90 days')
```

**Notes:** Dormant admin = an active user holding an admin entitlement with no activity in the last 90 days.

## ex-070
**Q:** Which active users have never had any recorded activity?
**Tags:** dormant, activity, empty-ok

```sql
SELECT u.name, u.email, u.department FROM users u
WHERE u.status = 'active' AND NOT EXISTS (SELECT 1 FROM activity_events a WHERE a.user_id = u.id)
ORDER BY u.name
```

## ex-071
**Q:** When was each privileged group member last active?
**Tags:** dormant, activity, privileged

```sql
SELECT u.name, u.email, GROUP_CONCAT(DISTINCT g.name) AS privileged_groups,
       (SELECT MAX(ts) FROM activity_events a WHERE a.user_id = u.id) AS last_activity
FROM users u JOIN group_members gm ON gm.user_id = u.id JOIN groups g ON g.id = gm.group_id
WHERE g.is_privileged = 1 AND u.status = 'active'
GROUP BY u.id ORDER BY last_activity
```

## ex-072
**Q:** Which dormant admins have admin rights on high-criticality applications?
**Tags:** dormant, admin, criticality

```sql
SELECT DISTINCT u.name, u.email, a.name AS application, e.name AS entitlement
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'active' AND e.is_admin = 1 AND a.criticality = 'high'
  AND COALESCE((SELECT MAX(ts) FROM activity_events ev WHERE ev.user_id = u.id), '') < date('2026-11-01', '-90 days')
ORDER BY u.name, a.name
```

## ex-073
**Q:** Which users hold admin access but have never performed an admin action?
**Tags:** admin, activity, admin_action

```sql
SELECT u.name, u.email, u.status
FROM users u
WHERE EXISTS (SELECT 1 FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id
              WHERE g.user_id = u.id AND e.is_admin = 1)
  AND NOT EXISTS (SELECT 1 FROM activity_events ev WHERE ev.user_id = u.id AND ev.event_type = 'admin_action')
ORDER BY u.name
```

## ex-074
**Q:** Which active users have admin access to an application they haven't used in the last 90 days?
**Tags:** admin, activity, unused

```sql
SELECT DISTINCT u.name, u.email, a.name AS application, e.name AS entitlement
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'active' AND e.is_admin = 1
  AND NOT EXISTS (SELECT 1 FROM activity_events ev
                  WHERE ev.user_id = u.id AND ev.app_id = a.id AND ev.ts >= date('2026-11-01', '-90 days'))
ORDER BY u.name, a.name
```

**Notes:** Per user AND per application: join on activity for that app, not on the grant date.

## ex-075
**Q:** Who has admin access to Snowflake but hasn't used it in the last 90 days?
**Tags:** admin, activity, specific-app, unused

```sql
SELECT DISTINCT u.name, u.email, u.status
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE a.name = 'Snowflake' AND e.is_admin = 1
  AND NOT EXISTS (SELECT 1 FROM activity_events ev
                  WHERE ev.user_id = u.id AND ev.app_id = a.id AND ev.ts >= date('2026-11-01', '-90 days'))
ORDER BY u.name
```

## ex-076
**Q:** For each department, how many active users hold admin access?
**Tags:** admin, department, group-by

```sql
SELECT u.department, COUNT(DISTINCT u.id) AS admins
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
WHERE u.status = 'active' AND e.is_admin = 1 GROUP BY u.department ORDER BY admins DESC
```

## ex-077
**Q:** Which active members of privileged groups don't have MFA enabled?
**Tags:** mfa, privileged, groups

```sql
SELECT u.name, u.email, u.department, GROUP_CONCAT(g.name, ', ') AS privileged_groups
FROM users u JOIN group_members gm ON gm.user_id = u.id JOIN groups g ON g.id = gm.group_id
WHERE g.is_privileged = 1 AND u.status = 'active' AND u.mfa_enabled = 0
GROUP BY u.id ORDER BY u.name
```

**Notes:** One row per user (a user can be in several privileged groups).

## ex-078
**Q:** How many privileged users lack MFA?
**Tags:** mfa, privileged, count

```sql
SELECT COUNT(DISTINCT u.id) AS privileged_users_without_mfa
FROM users u JOIN group_members gm ON gm.user_id = u.id JOIN groups g ON g.id = gm.group_id
WHERE g.is_privileged = 1 AND u.status = 'active' AND u.mfa_enabled = 0
```

## ex-079
**Q:** Which privileged groups have the most members without MFA?
**Tags:** mfa, privileged, group-by

```sql
SELECT g.name AS privileged_group, COUNT(*) AS members_without_mfa
FROM groups g JOIN group_members gm ON gm.group_id = g.id JOIN users u ON u.id = gm.user_id
WHERE g.is_privileged = 1 AND u.status = 'active' AND u.mfa_enabled = 0
GROUP BY g.id ORDER BY members_without_mfa DESC
```

## ex-080
**Q:** Which departments have the most privileged users without MFA?
**Tags:** mfa, privileged, department, group-by

```sql
SELECT u.department, COUNT(DISTINCT u.id) AS privileged_users_without_mfa
FROM users u JOIN group_members gm ON gm.user_id = u.id JOIN groups g ON g.id = gm.group_id
WHERE g.is_privileged = 1 AND u.status = 'active' AND u.mfa_enabled = 0
GROUP BY u.department ORDER BY privileged_users_without_mfa DESC
```

## ex-081
**Q:** Do any users with directly granted admin access lack MFA?
**Tags:** mfa, admin, direct

```sql
SELECT DISTINCT u.name, u.email, a.name AS application, e.name AS entitlement
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'active' AND u.mfa_enabled = 0 AND e.is_admin = 1 AND g.granted_via = 'direct'
ORDER BY u.name
```

## ex-082
**Q:** Which users with admin access to high-criticality applications have no MFA?
**Tags:** mfa, admin, criticality

```sql
SELECT DISTINCT u.name, u.email, a.name AS application
FROM users u JOIN access_grants g ON g.user_id = u.id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE u.status = 'active' AND u.mfa_enabled = 0 AND e.is_admin = 1 AND a.criticality = 'high'
ORDER BY u.name, a.name
```

## ex-083
**Q:** Which users get admin access only through a group and not directly?
**Tags:** admin, groups, inherited

```sql
SELECT DISTINCT u.name, u.email, a.name AS application, e.name AS entitlement, grp.name AS via_group
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id JOIN groups grp ON grp.id = g.group_id
WHERE e.is_admin = 1 AND g.granted_via = 'group' AND u.status = 'active'
ORDER BY u.name
```

## ex-084
**Q:** How many activity events were recorded in the last 30 days?
**Tags:** activity, count, recent

```sql
SELECT COUNT(*) AS events FROM activity_events WHERE ts >= date('2026-11-01', '-30 days')
```

## ex-085
**Q:** How many events are there of each type?
**Tags:** activity, group-by, event_type

```sql
SELECT event_type, COUNT(*) AS events FROM activity_events GROUP BY event_type ORDER BY events DESC
```

## ex-086
**Q:** Which applications had the most activity in the last 30 days?
**Tags:** activity, applications, top-n

```sql
SELECT a.name AS application, COUNT(*) AS events
FROM activity_events ev JOIN applications a ON a.id = ev.app_id
WHERE ev.ts >= date('2026-11-01', '-30 days')
GROUP BY a.id ORDER BY events DESC LIMIT 10
```

## ex-087
**Q:** What is the overall failed login rate?
**Tags:** activity, login, failed, percentage

```sql
SELECT ROUND(100.0 * SUM(success = 0) / COUNT(*), 2) AS failed_login_pct
FROM activity_events WHERE event_type = 'login'
```

## ex-088
**Q:** Which users had the most failed logins in the last 90 days?
**Tags:** activity, login, failed, top-n

```sql
SELECT u.name, u.email, COUNT(*) AS failed_logins
FROM activity_events ev JOIN users u ON u.id = ev.user_id
WHERE ev.event_type = 'login' AND ev.success = 0 AND ev.ts >= date('2026-11-01', '-90 days')
GROUP BY u.id ORDER BY failed_logins DESC, u.name LIMIT 10
```

## ex-089
**Q:** Which users logged in from outside the internal 10.x network?
**Tags:** activity, ip, login

```sql
SELECT u.name, u.email, COUNT(*) AS external_logins
FROM activity_events ev JOIN users u ON u.id = ev.user_id
WHERE ev.event_type = 'login' AND ev.ip NOT LIKE '10.%'
GROUP BY u.id ORDER BY external_logins DESC, u.name LIMIT 20
```

## ex-090
**Q:** How many admin actions happened in each month of 2026?
**Tags:** activity, admin_action, time, group-by

```sql
SELECT strftime('%Y-%m', ts) AS month, COUNT(*) AS admin_actions
FROM activity_events WHERE event_type = 'admin_action' AND ts >= '2026-01-01'
GROUP BY month ORDER BY month
```

## ex-091
**Q:** Which users performed the most admin actions?
**Tags:** activity, admin_action, top-n

```sql
SELECT u.name, u.email, COUNT(*) AS admin_actions
FROM activity_events ev JOIN users u ON u.id = ev.user_id
WHERE ev.event_type = 'admin_action' GROUP BY u.id ORDER BY admin_actions DESC, u.name LIMIT 10
```

## ex-092
**Q:** Show daily event counts for the last 7 days.
**Tags:** activity, time, daily

```sql
SELECT date(ts) AS day, COUNT(*) AS events FROM activity_events
WHERE ts >= date('2026-11-01', '-7 days') GROUP BY day ORDER BY day
```

## ex-093
**Q:** Which applications do Engineering users use the most?
**Tags:** activity, department, applications

```sql
SELECT a.name AS application, COUNT(*) AS events
FROM activity_events ev JOIN users u ON u.id = ev.user_id JOIN applications a ON a.id = ev.app_id
WHERE u.department = 'Engineering' GROUP BY a.id ORDER BY events DESC LIMIT 10
```

## ex-094
**Q:** How many distinct users were active in each of the last 3 months?
**Tags:** activity, time, distinct-users

```sql
SELECT strftime('%Y-%m', ts) AS month, COUNT(DISTINCT user_id) AS active_users
FROM activity_events WHERE ts >= date('2026-11-01', '-3 months') GROUP BY month ORDER BY month
```

## ex-095
**Q:** Which users had failed admin actions?
**Tags:** activity, admin_action, failed

```sql
SELECT u.name, u.email, COUNT(*) AS failed_admin_actions
FROM activity_events ev JOIN users u ON u.id = ev.user_id
WHERE ev.event_type = 'admin_action' AND ev.success = 0
GROUP BY u.id ORDER BY failed_admin_actions DESC, u.name
```

## ex-096
**Q:** Which hour of the day has the most logins?
**Tags:** activity, login, time, group-by

```sql
SELECT strftime('%H', ts) AS hour, COUNT(*) AS logins FROM activity_events
WHERE event_type = 'login' GROUP BY hour ORDER BY logins DESC LIMIT 5
```

## ex-097
**Q:** What share of grants are admin grants, by application criticality?
**Tags:** admin, criticality, group-by

```sql
SELECT a.criticality, COUNT(*) AS grants, SUM(e.is_admin) AS admin_grants,
       ROUND(100.0 * SUM(e.is_admin) / COUNT(*), 1) AS admin_pct
FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id JOIN applications a ON a.id = e.app_id
GROUP BY a.criticality ORDER BY admin_pct DESC
```

## ex-098
**Q:** Which 5 applications have the most users with admin access?
**Tags:** admin, applications, top-n

```sql
SELECT a.name AS application, COUNT(DISTINCT g.user_id) AS admin_users
FROM access_grants g JOIN entitlements e ON e.id = g.entitlement_id JOIN applications a ON a.id = e.app_id
WHERE e.is_admin = 1 GROUP BY a.id ORDER BY admin_users DESC LIMIT 5
```

## ex-099
**Q:** Which department has the most admin grants?
**Tags:** admin, department, top-n

```sql
SELECT u.department, COUNT(*) AS admin_grants
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
WHERE e.is_admin = 1 GROUP BY u.department ORDER BY admin_grants DESC LIMIT 1
```

## ex-100
**Q:** Which Sales users have access to Snowflake?
**Tags:** access_grants, department, application

```sql
SELECT DISTINCT u.name, u.email, e.name AS entitlement
FROM access_grants g JOIN users u ON u.id = g.user_id JOIN entitlements e ON e.id = g.entitlement_id
JOIN applications a ON a.id = e.app_id
WHERE a.name = 'Snowflake' AND u.department = 'Sales' AND u.status = 'active' ORDER BY u.name
```
