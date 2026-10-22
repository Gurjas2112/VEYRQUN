"""Interop: the real Python control plane + the real TypeScript gateway + mock upstreams.

Verifies the contract between the two halves: Ed25519 agent JWTs and JWKS, bundles signed in Python and
verified in Node, Redis Lua counters shared by both, decision events on the stream, approvals through the
internal API, the audit writer chaining gateway events, and kill-switch propagation from the control plane to the gateway (measured).

Prereqs: docker compose up -d postgres redis ; npm --prefix gateway ci
Run:     python -m pytest e2e/test_gateway_interop.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
CP, GW = "http://127.0.0.1:8011", "http://127.0.0.1:8091"
OWNER = "postgresql+psycopg://veyrqun_owner:veyrqun_owner_dev@localhost:55432"
TOKEN = "interop-service-token"
RESULTS = ROOT / "docs" / "results"
NPX = shutil.which("npx") or "npx"


def _wait(url: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up")


@pytest.fixture(scope="module")
def stack():
    admin = create_engine(f"{OWNER}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS veyrqun_interop WITH (FORCE)"))
        c.execute(text("CREATE DATABASE veyrqun_interop"))
        c.execute(text("GRANT CONNECT ON DATABASE veyrqun_interop TO veyrqun_app"))
    import redis

    redis.Redis.from_url("redis://localhost:56379/10").flushdb()  # fresh DB, so no stream events from earlier runs
    cp_env = {**os.environ, "VEYRQUN_ENV": "dev", "VEYRQUN_SIMULATOR": "0", "DEFAULT_WORKSPACE_ENV": "staging",
              "DATABASE_URL": "postgresql+psycopg://veyrqun_app:veyrqun_app_dev@localhost:55432/veyrqun_interop",
              "MIGRATION_DATABASE_URL": f"{OWNER}/veyrqun_interop", "REDIS_URL": "redis://localhost:56379/10",
              "BUNDLE_SERVICE_TOKEN": TOKEN, "LOG_LEVEL": "WARNING"}
    os.environ.update({k: v for k, v in cp_env.items() if k != "LOG_LEVEL"})  # this process talks to the same DB
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "control-plane", env=cp_env,
                   check=True, capture_output=True)
    gw_env = {**os.environ, "NODE_ENV": "test", "LOG_LEVEL": "warn", "PORT": "8091", "HOST": "127.0.0.1",
              "CONTROL_PLANE_URL": CP, "GATEWAY_SERVICE_TOKEN": TOKEN, "REDIS_URL": "redis://localhost:56379/10",
              "UPSTREAM_LLM_URLS": "http://127.0.0.1:9201", "UPSTREAM_MCP_URL": "http://127.0.0.1:9202/mcp"}
    logs = ROOT / "e2e"
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", "8011", "--log-level", "warning"],
                         cwd=ROOT / "control-plane", env=cp_env, stdout=open(logs / "interop-cp.log", "w"), stderr=subprocess.STDOUT),
        subprocess.Popen([NPX, "tsx", "src/mock/llm.ts"], cwd=ROOT / "gateway", env={**os.environ, "PORT": "9201"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=os.name == "nt"),
        subprocess.Popen([NPX, "tsx", "src/mock/bankcore-mcp.ts"], cwd=ROOT / "gateway", env={**os.environ, "PORT": "9202"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=os.name == "nt"),
    ]
    _wait(f"{CP}/health")
    procs.append(subprocess.Popen([NPX, "tsx", "src/index.ts"], cwd=ROOT / "gateway", env=gw_env,
                                  stdout=open(logs / "interop-gw.log", "w"), stderr=subprocess.STDOUT, shell=os.name == "nt"))
    _wait(f"{GW}/healthz")
    _wait("http://127.0.0.1:9201/v1/chat/completions")

    sys.path.insert(0, str(ROOT / "control-plane"))
    from veyrqun import service

    service.reset_runtime_state()
    _, _, creds = service.create_agent({
        "agent_id": "support_bot", "display_name": "Support Bot",
        "allowed_actions": ["llm.chat", "waive_fee", "credit_limit_increase", "account_summary"],
        "blocked_actions": ["wire_transfer"], "spend_cap_daily": Decimal("1000"), "max_per_transaction": Decimal("900"),
        "approval_threshold": Decimal("500"), "redact_data_classes": ["email"], "block_data_classes": ["pci_pan"],
    })
    token = httpx.post(f"{CP}/oauth/token", json={"grant_type": "client_credentials", "client_id": creds["client_id"],
                                                   "client_secret": creds["client_secret"]}).json()["access_token"]
    yield {"token": token, "service": service}
    for p in procs:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.terminate()


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
