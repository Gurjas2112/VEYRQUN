# Technical Debt & Roadmap

## Resolved in v2

| Previous issue | Resolution |
|---|---|
| 680-line `app.py` | Split into the `veyrqun` package (engine, service, audit, metrics, routes, simulator) |
| 800-line `App.jsx` with a syntax error | Split into components + hooks; build and lint clean |
| Simulator duplicated policy logic | Simulator and API share `service.attempt_action` |
| Simulator swallowed all errors | Logged via `logging.exception` |
| No tests | 45 pytest tests covering accuracy, concurrency, auditability, latency |
| New DB connection per call | Pooled per-thread connections (~60× lower decision latency) |
| No revocation distinct from stop | `revoked` status that survives fleet resume |
| Only a daily cap | + per-transaction, per-action caps and rate limits |
| Policy versions not browsable | `policy_versions` table, history UI, forward-only rollback |
| Operator actions not audited | Every control-plane action is an `operator:*` chain entry |

## Open

| # | Issue | Production fix |
|---|---|---|
| 1 | No authentication / authorisation on the control plane | OIDC + role-based access (operator vs. auditor), mTLS for agents |
| 2 | Single-process lock & SQLite | PostgreSQL with per-agent row locks; horizontal API replicas |
| 3 | Rate windows and metrics are in-memory (reset on restart) | Redis sliding windows; Prometheus + Grafana |
| 4 | "Daily" spend resets manually | Scheduled rollover per agent timezone |
| 5 | Hash chain is tamper-evident, not tamper-proof | WORM storage + external notarisation of the head hash |
| 6 | Operator identity not recorded in audit entries | Add `actor` to the hashed fields once auth exists |
| 7 | Rules are hand-coded | Compile the same ordered rules to OPA/Cedar; keep the pure-function contract |
