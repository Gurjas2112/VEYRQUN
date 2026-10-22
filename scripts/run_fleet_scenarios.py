"""Run the LangGraph fleet scenarios against a local stack and write docs/results/scenarios.json.

  docker compose up -d postgres redis && npm --prefix gateway ci && pip install -r agents/requirements.txt
  python scripts/run_fleet_scenarios.py

Each scenario's expected gateway answers are declared in agents/fleet/scenarios.py before it runs; this records the
actual trace next to them, then drains the audit writer and verifies the ledger.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))

from stack import fleet_env, local_stack  # noqa: E402


def main() -> None:
    with local_stack(name="fleet", db="veyrqun_fleet", redis_db=11, cp_port=8013, gw_port=8093, llm_port=9211,
                     mcp_port=9212, service_token="fleet-scenarios-token") as st:  # noqa: S106 (throwaway local stack)
        env = fleet_env(st)
        from fleet.scenarios import run_all

        scenarios = run_all(env)
        from veyrqun import audit, audit_writer

        writer = audit_writer.AuditWriter(consumer="fleet-scenarios", put=None).drain()
        chain = audit.verify_chain()
        ws = st.service.default_workspace()
        decision_ids = [d for r in scenarios for d in r["decision_ids"]]
        in_ledger = sum(audit.by_decision(d, ws) is not None for d in decision_ids)

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "host": {"os": platform.platform(), "cpu_count": os.cpu_count()},
        "setup": {"llm": "deterministic mock (gateway/src/mock/llm.ts)", "tools": "mock bank-core MCP server",
                  "agents": "LangGraph StateGraph, Postgres checkpointer", "approver": "control-plane service layer"},
        "summary": {
            "scenarios": len(scenarios),
            "passed": sum(r["passed"] for r in scenarios),
            "paused_for_approval": sum(r["paused_for_approval"] for r in scenarios),
            "resumed_from_checkpoint": sum(r["resumed_from_checkpoint"] for r in scenarios),
            "governed_calls": len(decision_ids),
            "governed_calls_in_ledger": in_ledger,
            "ledger_valid": chain["valid"],
            "audit_dead_letters": writer["dead_lettered"],
        },
        "scenarios": scenarios,
    }
    out = ROOT / "docs" / "results" / "scenarios.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))
    for r in scenarios:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']}")
    if result["summary"]["passed"] != len(scenarios):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
