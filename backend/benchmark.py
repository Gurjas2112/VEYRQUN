"""Measure VEYRQUN enforcement overhead.

Usage (from repo root):  python backend/benchmark.py [iterations]
Uses a throwaway database; prints p50/p95/p99 for the pure policy engine,
the full decision path, and the audit-chain verifier.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["VEYRQUN_DB_FILE"] = os.path.join(tempfile.mkdtemp(), "bench.db")

from veyrqun import audit, service  # noqa: E402
from veyrqun.db import SEED_AGENTS, init_db  # noqa: E402
from veyrqun.policy_engine import evaluate_policy  # noqa: E402


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p / 100 * (len(values) - 1)))]


def report(name, samples_ns):
    us = [s / 1000 for s in samples_ns]
    print(f"{name:<38} {len(us):>7}  p50 {pct(us, 50):>9.2f} µs  p95 {pct(us, 95):>9.2f} µs  p99 {pct(us, 99):>9.2f} µs")


def main(iterations: int) -> None:
    init_db()
    agent = {**SEED_AGENTS[0], "spend_used_today": 0.0, "status": "active", "policy_version": 1}

    samples = []
    for i in range(iterations * 10):
        t = time.perf_counter_ns()
        evaluate_policy(agent, "flight_rebook", float(i % 900), recent_actions=i % 12)
        samples.append(time.perf_counter_ns() - t)
    report("policy engine (pure evaluation)", samples)

    service.update_policy("travel_agent_01", {"rate_limit_per_minute": None, "spend_cap_daily": 1e12, "max_per_transaction": None})
    samples = []
    for i in range(iterations):
        t = time.perf_counter_ns()
        service.attempt_action("travel_agent_01", "flight_rebook" if i % 4 else "credit_limit_increase", 1.0)
        samples.append(time.perf_counter_ns() - t)
    report("full decision (eval + DB + audit)", samples)

    t = time.perf_counter_ns()
    result = audit.verify_chain()
    elapsed_ms = (time.perf_counter_ns() - t) / 1e6
    print(f"{'audit chain verify':<38} {result['entries_checked']:>7}  total {elapsed_ms:.1f} ms  valid={result['valid']}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000)
