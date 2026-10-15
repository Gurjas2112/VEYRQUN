# Backend

Python 3.11+, FastAPI, SQLite. No external services.

## Layout

```
backend/
├── app.py                 # entry shim: `uvicorn app:app` or `uvicorn backend.app:app`
├── benchmark.py           # latency benchmark (python backend/benchmark.py [n])
├── veyrqun/
│   ├── main.py            # FastAPI app, lifespan (init DB, start simulator), error handler
│   ├── config.py          # env-driven settings (DB path, CORS, simulator on/off)
│   ├── db.py              # schema, migrations, seed fleet, pooled connections, write_lock
│   ├── models.py          # Pydantic request models + validation
│   ├── policy_engine.py   # pure evaluate_policy() — the rule order lives here
│   ├── service.py         # every state change: decisions + operator actions
│   ├── audit.py           # SHA-256 chained ledger: append / query / verify
│   ├── metrics.py         # decision counters, latency percentiles, Prometheus text
│   ├── simulator.py       # background agent traffic
│   ├── ws.py              # WebSocket connection manager
│   └── routes/            # agents · actions · control · audit · metrics
└── tests/                 # pytest suite (see Testing.md)
```

## Key design choices

**Pure policy engine.** `evaluate_policy(agent, action_type, amount, fleet_stopped, recent_actions)` touches no DB
or clock, so it is exhaustively unit-tested and benchmarked in isolation (~1 µs p99).

**One choke point.** Routes are thin; `service.py` is the only module that mutates state. Each mutation runs under
`db.write_lock`, in one SQLite transaction that also appends the audit entry — so a decision and its log entry are
committed together or not at all.

**Hash chain.** `entry_hash = SHA-256(json.dumps({action_id, agent_id, action_type, amount, timestamp, outcome,
rule_triggered, policy_version, prev_hash}, sort_keys=True))`. The first entry's `prev_hash` is `GENESIS`.
`verify_chain()` recomputes every hash and link and reports the first `broken_at` entry — catching edits, deletions,
reordering, and even re-hashed forgeries (the next link breaks).

**Policy versioning.** Every update writes a row to `policy_versions` and bumps `agents.policy_version`; each audit
entry stamps the version in force. Rollback is forward-only — it re-publishes old rules as a *new* version, so history
is never rewritten.

**Explicit null clears a limit.** `PATCH /agents/{id}/policy` uses Pydantic's `model_fields_set`, so
`{"max_per_transaction": null}` removes the limit while omitting the field leaves it untouched.

**Errors.** Service code raises `GovernanceError(status, detail)`; a single exception handler maps it to JSON.

## Seeded fleet

| Agent | Allowed | Blocked | Daily cap | Per-tx | Rate | Action caps |
|---|---|---|---|---|---|---|
| `travel_agent_01` | flight_rebook, hotel_rebook | credit_limit_increase | $1,500 | $600 | 10/min | — |
| `fee_reversal_02` | waive_fee, account_summary | wire_transfer | $500 | $100 | 10/min | waive_fee ≤ $50 |
| `credit_limit_03` | credit_limit_increase, score_check | card_issue | $5,000 | $2,500 | 6/min | — |
| `card_replacement_04` | card_issue, card_lock | waive_fee | $200 | $40 | 10/min | — |
| `balance_check_05` | account_summary, balance_check | wire_transfer, flight_rebook | $100 | — | 4/min | — |

The simulator picks a random agent every 2–4 s with a persona-plausible action (amount jittered 0.8–1.5×), producing
a realistic mix of approvals and blocks across every rule type.
