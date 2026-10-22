"""Interop: the real Python control plane + the real TypeScript gateway + mock upstreams.

Verifies the contract between the two halves: Ed25519 agent JWTs and JWKS, bundles signed in Python and
verified in Node, Redis Lua counters shared by both, decision events on the stream, approvals through the
internal API, the audit writer chaining gateway events, and kill-switch propagation from the control plane to the gateway (measured).

Prereqs: docker compose up -d postgres redis ; npm --prefix gateway ci  (the stack itself: e2e/stack.py)
Run:     python -m pytest e2e/test_gateway_interop.py -q
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from stack import local_stack

ROOT = Path(__file__).resolve().parents[1]
CP, GW = "http://127.0.0.1:8011", "http://127.0.0.1:8091"
TOKEN = "interop-service-token"
RESULTS = ROOT / "docs" / "results"


@pytest.fixture(scope="module")
def stack():
    with local_stack(name="interop", db="veyrqun_interop", redis_db=10, cp_port=8011, gw_port=8091, llm_port=9201,
                     mcp_port=9202, service_token=TOKEN) as st:
        service = st.service
        _, _, creds = service.create_agent({
            "agent_id": "support_bot", "display_name": "Support Bot",
            "allowed_actions": ["llm.chat", "waive_fee", "credit_limit_increase", "account_summary"],
            "blocked_actions": ["wire_transfer"], "spend_cap_daily": Decimal("1000"),
            "max_per_transaction": Decimal("900"), "approval_threshold": Decimal("500"),
            "redact_data_classes": ["email"], "block_data_classes": ["pci_pan"],
        })
        token = httpx.post(f"{CP}/oauth/token", json={"grant_type": "client_credentials", "client_id": creds["client_id"],
                                                       "client_secret": creds["client_secret"]}).json()["access_token"]
        yield {"token": token, "service": service}


def call(stack, path: str, body: dict, **headers) -> httpx.Response:
    return httpx.post(f"{GW}{path}", json=body, timeout=10,
                      headers={"authorization": f"Bearer {stack['token']}", **headers})


def tool(name: str, **args) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}


def test_agent_token_from_the_control_plane_is_accepted_by_the_gateway(stack):
    r = call(stack, "/v1/chat/completions", {"model": "mock-model", "messages": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 200 and r.headers["x-veyrqun-decision-id"]


def test_python_signed_bundle_enforced_by_node(stack):
    assert call(stack, "/mcp", tool("waive_fee", amount="25.00")).status_code == 200
    denied = call(stack, "/mcp", tool("wire_transfer", amount="5.00"))
    assert denied.status_code == 403
    assert denied.json()["error"]["data"]["rule_id"] == "action_not_permitted"


def test_policy_change_in_the_control_plane_reaches_the_gateway(stack):
    service = stack["service"]
    service.update_policy("support_bot", {"blocked_actions": ["wire_transfer", "waive_fee"],
                                          "allowed_actions": ["llm.chat", "credit_limit_increase", "account_summary"]})
    time.sleep(0.5)
    assert call(stack, "/mcp", tool("waive_fee", amount="1.00")).status_code == 403
    service.update_policy("support_bot", {"blocked_actions": ["wire_transfer"],
                                          "allowed_actions": ["llm.chat", "waive_fee", "credit_limit_increase", "account_summary"]})
    time.sleep(0.5)
    assert call(stack, "/mcp", tool("waive_fee", amount="1.00")).status_code == 200


def test_approval_round_trip_through_the_control_plane(stack):
    service = stack["service"]
    held = call(stack, "/mcp", tool("credit_limit_increase", increase="600.00"))
    assert held.status_code == 202
    approval_id = held.json()["approval_id"]
    service.decide_approval(approval_id, True, actor="user:interop-approver")
    ok = call(stack, "/mcp", tool("credit_limit_increase", increase="600.00"), **{"x-veyrqun-approval": approval_id})
    assert ok.status_code == 200
    assert service.get_approval(approval_id, service.default_workspace())["status"] == "consumed"


def test_decision_events_reach_the_stream(stack):
    import redis

    r = redis.Redis.from_url("redis://localhost:56379/10", decode_responses=True)
    last = json.loads(r.xrevrange("vq:decisions", count=1)[0][1]["event"])
    assert last["agent_id"] == "support_bot" and last["workspace_id"] == str(stack["service"].default_workspace())


def test_audit_writer_chains_gateway_decisions(stack):
    """Gateway (Node) -> Redis Stream -> audit writer (Python) -> HMAC-signed, anchored, verifiable ledger."""
    from veyrqun import audit, audit_writer

    stats = audit_writer.AuditWriter(consumer="interop", put=None).drain()
    assert stats["leader"] and stats["written"] > 0 and stats["dead_lettered"] == 0
    entries = audit.query(stack["service"].default_workspace(), agent_id="support_bot", limit=200)
    gateway = [e for e in entries if e["details"].get("source") == "gateway"]
    assert gateway and all(e["hash_version"] == 2 and e["hmac"] for e in gateway)
    result = audit.verify_chain()
    assert result["valid"] and result["anchors_checked"] >= 1
    assert audit_writer.AuditWriter(consumer="interop-2", put=None).drain()["written"] == 0  # nothing left, no dupes


def test_kill_switch_propagation_from_control_plane_to_gateway(stack):
    service = stack["service"]
    samples = []
    for _ in range(5):
        assert call(stack, "/mcp", tool("account_summary")).status_code == 200
        t0 = time.perf_counter()
        service.fleet_stop(actor="user:interop")
        while call(stack, "/mcp", tool("account_summary")).status_code != 403:
            if time.perf_counter() - t0 > 3:
                raise AssertionError("kill switch did not propagate within 3 s")
        samples.append((time.perf_counter() - t0) * 1000)
        service.fleet_resume(actor="user:interop")
        while call(stack, "/mcp", tool("account_summary")).status_code != 200:
            time.sleep(0.02)
    samples.sort()
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "control-plane fleet_stop() -> first 403 at the gateway (polling client); includes the client's "
                  "own request round-trip. Single host, Redis pub/sub + 1 s poll backstop.",
        "samples_ms": [round(s, 1) for s in samples],
        "max_ms": round(samples[-1], 1),
        "median_ms": round(samples[len(samples) // 2], 1),
        "target_ms": 1000,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "kill_switch.json").write_text(json.dumps(result, indent=2) + "\n")
    assert result["max_ms"] < 1000
