# 0008. Tenant isolation in the database (row-level security), not only in the app

**Status:** accepted · 2026-10-22

## Context
A single missed `WHERE workspace_id = …` in application code would leak another tenant's agents, decisions or approvals.

## Decision
- Every tenant table has a `workspace_id` and a Postgres RLS policy: rows are visible and writable only when
  `current_setting('app.workspace_id')` equals the row's workspace, or is `'*'` (system scope for seeding, chain
  verification and background jobs). Child tables (`policy_versions`, `spend_snapshots`) are isolated through their agent.
- `db.tx(scope)` sets the scope per transaction (`set_config(..., true)` = `SET LOCAL`). **No scope means no rows**
  (fail closed).
- The app connects as `veyrqun_app`, which is not the table owner, so RLS applies. Migrations run as the owner.
- The live WebSocket feed filters by the session's workspace too.

## Consequences
Tests prove cross-tenant reads return 0 rows at the database, cross-tenant writes are rejected, and id-swap (IDOR)
requests for another tenant's agents, approvals and audit entries return 404. Global chain verification uses system scope
and returns only `valid` / `broken_at`.
