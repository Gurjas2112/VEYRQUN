# Testing & Performance

```bash
pip install -r requirements-dev.txt
python -m pytest            # 45 tests, ~1–4 s
python control-plane/benchmark.py # latency table
```

Every test runs against a fresh temporary database with the simulator disabled.

## Coverage by PS requirement

| Requirement | Tests |
|---|---|
| **Enforcement accuracy** | `test_policy_engine.py` — every rule, exact boundaries (cap reached exactly passes, +$0.01 blocks), float drift, rule precedence (fleet > revoked > stopped > deny > allow > tx-limit > rate > cap), purity |
| **Real-time spend caps** | `test_api.py::test_spend_cap_enforced_and_reset`, `test_rate_limit`, `test_explicit_null_clears_a_limit`, `test_concurrent_actions_never_overshoot_cap` (50 parallel requests → exactly 15 approvals on a $1,500 cap) |
| **Permissions & policy lifecycle** | `test_policy_update_takes_effect_immediately_and_bumps_version`, `test_policy_history_and_rollback`, `test_create_agent_and_duplicate`, `test_input_validation` |
| **Revocation & emergency stop** | `test_single_agent_stop_and_resume`, `test_fleet_emergency_stop_halts_everyone` (incl. single-agent resume cannot bypass fleet stop), `test_revocation_survives_fleet_resume` |
| **Auditability** | `test_audit_chain.py` — every decision logged, operator events chained, tampered amount detected, deleted row detected, re-hashed forgery detected at the next link; `test_audit_log_filters` |
| **Low latency** | `test_latency.py` — policy eval p99 < 50 µs over 10k runs; full decision p95 < 5 ms over 300 runs |
| **Observability** | `test_metrics_endpoints` (JSON + Prometheus), `test_websocket_streams_decisions` |

## Benchmark (Windows 11 laptop, Python 3.12)

```
policy engine (pure evaluation)          20000  p50      0.70 µs  p95      0.90 µs  p99      1.00 µs
full decision (eval + DB + audit)         2000  p50     97.70 µs  p95    157.00 µs  p99    653.50 µs
audit chain verify                        2001  total 15.6 ms  valid=True
```

### Optimisation log

| Change | Full-decision p50 |
|---|---|
| Baseline: new SQLite connection per request | 5,913 µs |
| `PRAGMA synchronous=NORMAL` (WAL) | 5,793 µs — fsync was not the bottleneck |
| Pooled per-thread connections (profiling showed open+close ≈ 1.1 ms) | **98 µs** (~60× faster) |

The live dashboard reports the same two latencies continuously (`policy_eval_latency`, `decision_latency`).
