"""Phase 4: the LangGraph fleet against the real control plane + gateway + mock upstreams.

Every scenario's expected gateway answers are written down in agents/fleet/scenarios.py; this test fails if any
actual trace differs. It also checks that every decision the agents caused is in the verified, signed ledger.

Prereqs: docker compose up -d postgres redis ; npm --prefix gateway ci ; pip install -r agents/requirements.txt
"""

from __future__ import annotations

import pytest
from stack import fleet_env, local_stack


@pytest.fixture(scope="module")
def results():
    with local_stack(name="fleet-e2e", db="veyrqun_fleet_e2e", redis_db=14, cp_port=8014, gw_port=8094,
                     llm_port=9221, mcp_port=9222, service_token="fleet-e2e-token") as st:
        env = fleet_env(st)  # also puts agents/ on sys.path
        from fleet.scenarios import run_all

        out = run_all(env)
        from veyrqun import audit, audit_writer

        stats = audit_writer.AuditWriter(consumer="fleet-e2e", put=None).drain()
        yield {"scenarios": out, "stats": stats, "chain": audit.verify_chain(), "audit": audit,
               "ws": st.service.default_workspace()}


def test_every_scenario_matches_its_expected_trace(results):
    failed = [(r["id"], r["expected"], r["actual"], r["final"]) for r in results["scenarios"] if not r["passed"]]
    assert not failed, failed


def test_approval_runs_pause_and_resume_from_the_postgres_checkpoint(results):
    paused = [r for r in results["scenarios"] if r["paused_for_approval"]]
    assert len(paused) == 2 and all(r["resumed_from_checkpoint"] for r in paused)


def test_every_agent_decision_is_in_the_signed_ledger(results):
    assert results["stats"]["dead_lettered"] == 0
    assert results["chain"]["valid"]
    audit, ws = results["audit"], results["ws"]
    ids = [d for r in results["scenarios"] for d in r["decision_ids"]]
    assert ids and all(audit.by_decision(d, ws) is not None for d in ids)
