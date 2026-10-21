# Data Models

SQLite file `control-plane/veyrqun.db` (override with `VEYRQUN_DB_FILE`). Created and seeded on startup; older databases
are migrated in place (new columns added).

## `agents`

| Column | Type | Notes |
|---|---|---|
| agent_id | TEXT PK | `^[a-z0-9][a-z0-9_-]{2,63}$` |
| display_name | TEXT | |
| allowed_actions | JSON list | default-deny allow-list |
| blocked_actions | JSON list | explicit deny; wins over allow |
| spend_cap_daily | REAL | "daily" = since last reset |
| spend_used_today | REAL | incremented only on approval |
| max_per_transaction | REAL NULL | per-action-attempt ceiling |
| rate_limit_per_minute | INTEGER NULL | approved actions per rolling 60 s |
| action_limits | JSON object | per-action ceilings, e.g. `{"waive_fee": 50}` |
| status | TEXT | `active` \| `stopped` \| `revoked` |
| status_reason | TEXT NULL | why stopped/revoked |
| policy_version | INTEGER | bumped on every policy change |

## `policy_versions`

Immutable history: `policy_id`, `agent_id`, `version` (unique per agent), `created_at`, every policy field above,
and `note` (e.g. `initial policy`, `rollback to v1`, operator-supplied change note).

## `audit_logs`

| Column | Notes |
|---|---|
| action_id | UUID4 |
| agent_id | agent, or `*fleet*` for fleet-wide operator events |
| action_type | agent action, or `operator:<event>` (`policy_update`, `policy_rollback_vN`, `emergency_stop`, `resume`, `revoke`, `reinstate`, `fleet_emergency_stop`, `fleet_resume`, `reset_spend`, `reset_all_spend`, `register_agent`) |
| amount | rounded to cents |
| timestamp | ISO-8601 UTC, ms precision |
| outcome | `approved` \| `blocked` \| `executed` (operator) |
| rule_triggered | rule id or NULL |
| policy_version | version in force at decision time |
| prev_hash | previous entry's hash, or `GENESIS` |
| entry_hash | SHA-256 of all fields above (sorted-key JSON) |

## `fleet_state`

Key/value; `fleet_stopped` = `"0"` / `"1"`.

## Rule identifiers

`fleet_emergency_stop_active`, `agent_revoked`, `emergency_stop_active`, `action_not_permitted`,
`action_not_in_allowlist`, `transaction_limit_exceeded`, `rate_limit_exceeded`, `spend_cap_exceeded` —
evaluated in exactly that order.
