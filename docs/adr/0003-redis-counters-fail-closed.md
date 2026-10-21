# 0003. Live counters in Redis (atomic Lua), keyed by local date; fail closed

**Status:** accepted · 2026-10-22

## Context
v2 enforced spend caps under a process-wide `RLock` with SQLite, which cannot scale past one process. Caps must hold
under concurrency across gateway replicas.

## Decision
- `policies/lua/reserve.lua` does check-and-increment atomically in order: rate window, then daily spend, then
  per-action daily spend. `release.lua` rolls a reservation back if the upstream call fails, and a double release is a
  no-op. The gateway and the control plane load the same scripts.
- Money is integer minor units in Redis and in the policy. Decimal / NUMERIC(18,2) is used at the edges and in Postgres.
- Keys embed the agent's **local date** (`vq:{ws:agent}:spend:YYYY-MM-DD`, TTL 2 days), so the daily rollover happens
  at local midnight in each agent's timezone with no scheduler. `{ws:agent}` is a Redis Cluster hash tag.
- Rego returns the limits, and Lua enforces them. Static rules (status, lists, per-transaction caps, approvals) never
  touch Redis.
- **Redis unreachable means block** (`state_store_unavailable`) for anything that carries spend or rate state.

## Consequences
- The global lock is gone. The test with 50 parallel $100 requests against a $1,500 cap gives exactly 15 approvals.
- Redis is the system of record for *live* counters only. A snapshot and reconciliation job against approved ledger
  entries (Phase 1B/6) covers Redis loss and restarts.
