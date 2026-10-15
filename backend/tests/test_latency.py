"""Latency budget tests: governance must add negligible overhead to agent operations."""

import time

from veyrqun import service
from veyrqun.policy_engine import evaluate_policy


def _p(values, pct):
    values = sorted(values)
    return values[min(len(values) - 1, int(pct / 100 * (len(values) - 1)))]


def test_policy_evaluation_p99_under_50_microseconds(base_agent):
    agent = {**base_agent, "max_per_transaction": 500.0, "rate_limit_per_minute": 100, "action_limits": {"refund": 50.0}}
    samples = []
    for i in range(10_000):
        t = time.perf_counter_ns()
        evaluate_policy(agent, "pay" if i % 3 else "refund", float(i % 700), recent_actions=i % 120)
        samples.append(time.perf_counter_ns() - t)
    assert _p(samples, 99) < 50_000


def test_full_decision_p95_under_5_milliseconds():
    """Includes lock, SQLite read/write, spend update and hash-chained audit append."""
    service.update_policy("travel_agent_01", {"rate_limit_per_minute": None, "spend_cap_daily": 1e9, "max_per_transaction": None})
    samples = []
    for _ in range(300):
        t = time.perf_counter_ns()
        service.attempt_action("travel_agent_01", "flight_rebook", 1.0)
        samples.append(time.perf_counter_ns() - t)
    assert _p(samples, 95) < 5_000_000
