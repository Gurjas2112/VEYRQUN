# Architecture

![Architecture diagram](../docs_images/architecture_diagram.png)

## Components

```
 Agents / simulator                Operators (dashboard, curl, SOAR playbooks)
        │ POST /simulate/action            │ policy · stop · revoke · resume · rollback
        ▼                                  ▼
 ┌──────────────────────────── FastAPI (veyrqun.main) ────────────────────────────┐
 │ routes/actions    routes/agents    routes/control    routes/audit   routes/metrics │
 │        └───────────────┬──────────────────┘                                     │
 │                 service.py  ── write_lock (serialises every state change)        │
 │                   │        │                     │                               │
 │         policy_engine.py   audit.py (SHA-256 chain)   metrics.py (counters + p50/p95/p99) │
 │                   └────────┴──────── db.py (SQLite WAL, pooled per-thread conns) │
 │                                                                                   │
 │ ws.py ── broadcasts every ledger entry + affected agent state ──► /ws/live-feed    │
 │ simulator.py ── asyncio loop generating realistic agent traffic                   │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

## Decision flow (data plane)

1. Agent calls `POST /simulate/action {agent_id, action_type, amount}` — the policy enforcement point.
2. `service.attempt_action` takes `write_lock`, loads the agent, reads the fleet flag and the agent's
   60-second rate window.
3. `policy_engine.evaluate_policy` (pure function, ~1 µs) applies rules in order — first hit wins:

   | # | Rule id | Blocks when |
   |---|---|---|
   | 1 | `fleet_emergency_stop_active` | fleet kill switch engaged |
   | 2 | `agent_revoked` | agent's credentials revoked |
   | 3 | `emergency_stop_active` | agent individually stopped |
   | 4 | `action_not_permitted` | action on the block-list (explicit deny wins) |
   | 5 | `action_not_in_allowlist` | action not on the allow-list (default deny) |
   | 6 | `transaction_limit_exceeded` | amount > per-transaction limit or per-action cap |
   | 7 | `rate_limit_exceeded` | approved actions in last 60 s ≥ rate limit |
   | 8 | `spend_cap_exceeded` | spend so far + amount > daily cap |

4. If approved, spend is incremented and the action is recorded in the rate window.
5. An audit entry (approved **or** blocked) is appended to the hash chain in the **same transaction**.
6. Commit, release lock, record latency, broadcast over WebSocket.

## Control plane

Every operator action — policy update, rollback, stop, resume, revoke, reinstate, fleet stop/resume, spend reset,
agent registration — also goes through `service.py`, under the same lock, and writes an `operator:*` entry into the
same hash chain. The audit log therefore answers both *"what did the agent do?"* and *"who changed the rules, and when?"*

## State model

```
            stop                    revoke
  active ─────────► stopped ──────────────────► revoked
    ▲ ◄───────────── │  resume / resume-all       │
    │                                             │ reinstate (explicit only)
    └─────────────────────────────────────────────┘
  fleet flag (independent): when set, every decision is blocked regardless of agent status
```

* **Stop** is a reversible pause; *Resume Fleet* lifts it.
* **Revoke** is a credential kill — it survives *Resume Fleet* and needs an explicit *Reinstate*.
* **Fleet emergency stop** sets a global flag *and* flips all active agents to stopped. Resuming one agent while the
  flag is set does **not** bypass it.

## Concurrency & consistency

* A single `RLock` serialises evaluate → spend update → audit append. Tested: 50 parallel $100 requests against a
  $1,500 cap approve exactly 15, and the chain still verifies.
* SQLite in WAL mode with `synchronous=NORMAL`; each thread keeps one pooled connection (opening a connection cost
  ~1 ms on Windows — more than the whole decision).

## Scaling path

| Today (demo) | Production |
|---|---|
| SQLite + in-process lock | PostgreSQL with `SELECT … FOR UPDATE` / serialisable txn per agent |
| In-memory rate windows & metrics | Redis sliding windows; Prometheus scrape of `/metrics/prometheus` |
| Python policy engine | Same rule order compiled to OPA/Cedar policies |
| In-process WebSocket fan-out | Redis pub/sub or Kafka topic |
| Hash chain in DB | Plus WORM storage and periodic head-hash notarisation |
