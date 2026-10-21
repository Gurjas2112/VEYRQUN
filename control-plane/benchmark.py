"""Measure control-plane decision latency on the v3 stack (OPA-WASM + Redis Lua + Postgres ledger).

Usage (repo root, with `docker compose up -d postgres redis` and migrations applied):
    python control-plane/benchmark.py [iterations]
Writes docs/results/control_plane_latency.json. The inline gateway has its own benchmark (loadtest/).
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))
os.environ.setdefault("VEYRQUN_SIMULATOR", "0")

from veyrqun import audit, service  # noqa: E402
from veyrqun.policy import engine  # noqa: E402
from veyrqun.policy.store import load_workspace  # noqa: E402


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(p / 100 * (len(s) - 1)))]


def summary(samples_ns: list[int]) -> dict:
    us = [s / 1000 for s in samples_ns]
    return {"samples": len(us), "p50_us": round(pct(us, 50), 1), "p95_us": round(pct(us, 95), 1),
            "p99_us": round(pct(us, 99), 1)}


def main(iterations: int) -> dict:
    ws = service.default_workspace()
    service.update_policy("travel_agent_01", {"rate_limit_per_minute": None, "spend_cap_daily": Decimal("999999999999"),
                                              "max_per_transaction": None}, note="benchmark")
    revision, data, _ = load_workspace(ws)
    inp = {"agent_id": "travel_agent_01", "action": "flight_rebook", "amount_minor": 100, "data_classes": []}
    engine.evaluate(ws, revision, data, inp)  # warm

    evals = []
    for i in range(iterations * 5):
        t = time.perf_counter_ns()
        engine.evaluate(ws, revision, data, {**inp, "amount_minor": i})
        evals.append(time.perf_counter_ns() - t)

    decisions = []
    for i in range(iterations):
        t = time.perf_counter_ns()
        service.attempt_action("travel_agent_01", "flight_rebook" if i % 4 else "credit_limit_increase", Decimal("1"))
        decisions.append(time.perf_counter_ns() - t)

    t = time.perf_counter_ns()
    chain = audit.verify_chain()
    verify_ms = (time.perf_counter_ns() - t) / 1e6

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "host": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        "policy_eval_wasm_python": summary(evals),
        "full_decision_eval_redis_postgres": summary(decisions),
        "audit_verify": {"entries": chain["entries_checked"], "valid": chain["valid"], "total_ms": round(verify_ms, 1)},
        "note": "Control-plane (legacy) path. Policy eval = compiled Rego via the Python wasmtime host. "
                "Full decision = eval + Redis Lua reservation + Postgres hash-chained append (one transaction).",
    }


if __name__ == "__main__":
    result = main(int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
    out = ROOT / "docs" / "results" / "control_plane_latency.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
