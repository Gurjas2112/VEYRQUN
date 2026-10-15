# API Reference

Base URL `http://localhost:8000`. Interactive docs at `/docs` (Swagger) and `/redoc`.
Errors are JSON `{"detail": "..."}` with 404 (unknown agent/version), 409 (conflict, e.g. resuming a revoked agent),
or 422 (validation).

## Agents & policies

| Method | Path | Description |
|---|---|---|
| GET | `/agents` | All agents with live status, spend and limits |
| GET | `/agents/{id}` | One agent |
| POST | `/agents` | Register an agent (starts `active`, policy v1) |
| PATCH | `/agents/{id}/policy` | Partial policy update → new version. `null` clears an optional limit |
| GET | `/agents/{id}/policy-history` | All versions, newest first |
| POST | `/agents/{id}/policy/rollback/{version}` | Re-publish an old version as a new one |
| POST | `/agents/{id}/reset-spend` | Reset daily spend to 0 |
| POST | `/reset-all-spend` | Reset every agent's spend |

```json
// POST /agents
{
  "agent_id": "dispute_agent_06",
  "display_name": "Dispute Resolution Agent",
  "allowed_actions": ["chargeback_open"],
  "blocked_actions": ["wire_transfer"],
  "spend_cap_daily": 2000,
  "max_per_transaction": 500,
  "rate_limit_per_minute": 5,
  "action_limits": {"chargeback_open": 400}
}

// PATCH /agents/fee_reversal_02/policy
{"spend_cap_daily": 250, "max_per_transaction": null, "note": "tighten after incident #142"}
```

## Agent actions (policy enforcement point)

| Method | Path | Description |
|---|---|---|
| POST | `/simulate/action` | `{agent_id, action_type, amount≥0}` → evaluated, logged, returned |
| GET | `/simulator` | `{simulator_running}` |
| POST | `/simulator/toggle?running=true\|false` | Pause/resume background traffic |
| WS | `/ws/live-feed` | Pushes every ledger entry the instant it's written |

```json
// response / WebSocket message
{
  "action_id": "0b8c…", "agent_id": "travel_agent_01", "action_type": "credit_limit_increase",
  "amount": 1000.0, "timestamp": "2026-10-15T14:21:27.521Z", "outcome": "blocked",
  "rule_triggered": "action_not_permitted", "policy_version": 2,
  "prev_hash": "4a25…", "entry_hash": "3f89…",
  "_agents": [ { …agent snapshot… } ], "_agent_snapshot": { … }   // WebSocket only
}
```

## Revocation & emergency stop

| Method | Path | Description |
|---|---|---|
| POST | `/emergency-stop` | Fleet kill switch: sets global flag, stops all active agents |
| POST | `/resume-all` | Clears flag, resumes stopped agents (revoked stay revoked) |
| POST | `/emergency-stop/{id}` | Stop one agent (optional body `{"reason": "..."}`) |
| POST | `/resume/{id}` | Resume one stopped agent (409 if revoked) |
| POST | `/agents/{id}/revoke` | Revoke credentials (optional `{"reason"}`) |
| POST | `/agents/{id}/reinstate` | Reinstate a revoked agent |
| GET | `/fleet/status` | `{fleet_stopped, agents_by_status}` |

## Audit

| Method | Path | Description |
|---|---|---|
| GET | `/audit-log?agent_id=&outcome=&action_type=&limit=50&offset=0` | Newest first. `outcome` ∈ approved, blocked, executed (operator) |
| GET | `/audit-log/verify` | `{"valid": true, "entries_checked": N, "head_hash": "…"}` or `{"valid": false, "broken_at": "<action_id>", "reason": "…"}` |

## Observability

| Method | Path | Description |
|---|---|---|
| GET | `/metrics` | Decision totals, approval rate, blocks by rule, per-agent counts, operator events, latency p50/p95/p99 (policy eval and full decision), fleet status |
| GET | `/metrics/prometheus` | Same, in Prometheus text exposition format |
| GET | `/health` | Liveness |
