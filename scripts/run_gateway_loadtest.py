"""Run the k6 gateway load test against a local stack and write docs/results/gateway_latency.json.

  docker compose up -d postgres redis && npm --prefix gateway ci && npm --prefix gateway run build
  python scripts/run_gateway_loadtest.py [--rate 200] [--duration 30s]

Starts the control plane, the compiled gateway (node dist/index.js) and the mock upstreams, creates a
load-test agent with no rate limit, and runs k6 (grafana/k6 in Docker). "Overhead" is the gateway's own
Server-Timing `gateway;dur` (total time inside the gateway minus the upstream call).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
OWNER = "postgresql+psycopg://veyrqun_owner:veyrqun_owner_dev@localhost:55432"
TOKEN = "loadtest-service-token"  # noqa: S105 (throwaway token for a throwaway local database)
K6_IMAGE = "grafana/k6:1.3.0"
NPX = shutil.which("npx") or "npx"


def wait(url: str) -> None:
    for _ in range(200):
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise SystemExit(f"{url} did not come up")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=int, default=200)
    ap.add_argument("--duration", default="30s")
    a = ap.parse_args()

    admin = create_engine(f"{OWNER}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS veyrqun_load WITH (FORCE)"))
        c.execute(text("CREATE DATABASE veyrqun_load"))
        c.execute(text("GRANT CONNECT ON DATABASE veyrqun_load TO veyrqun_app"))
    cp_env = {**os.environ, "VEYRQUN_ENV": "dev", "VEYRQUN_SIMULATOR": "0", "DEFAULT_WORKSPACE_ENV": "staging",
              "DATABASE_URL": "postgresql+psycopg://veyrqun_app:veyrqun_app_dev@localhost:55432/veyrqun_load",
              "MIGRATION_DATABASE_URL": f"{OWNER}/veyrqun_load", "REDIS_URL": "redis://localhost:56379/9",
              "BUNDLE_SERVICE_TOKEN": TOKEN, "LOG_LEVEL": "WARNING"}
    os.environ.update({k: v for k, v in cp_env.items() if k != "LOG_LEVEL"})
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "control-plane", env=cp_env, check=True)
    gw_env = {**os.environ, "NODE_ENV": "production", "LOG_LEVEL": "warn", "PORT": "8092", "HOST": "0.0.0.0",  # noqa: S104 (k6 in Docker reaches the host)
              "CONTROL_PLANE_URL": "http://127.0.0.1:8012", "GATEWAY_SERVICE_TOKEN": TOKEN,
              "REDIS_URL": "redis://localhost:56379/9", "UPSTREAM_LLM_URLS": "http://127.0.0.1:9301",
              "UPSTREAM_MCP_URL": "http://127.0.0.1:9302/mcp"}
    shell = os.name == "nt"
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", "8012", "--log-level", "warning"],
                         cwd=ROOT / "control-plane", env=cp_env),
        subprocess.Popen([NPX, "tsx", "src/mock/llm.ts"], cwd=ROOT / "gateway", env={**os.environ, "PORT": "9301"},
                         stdout=subprocess.DEVNULL, shell=shell),
        subprocess.Popen([NPX, "tsx", "src/mock/bankcore-mcp.ts"], cwd=ROOT / "gateway", env={**os.environ, "PORT": "9302"},
                         stdout=subprocess.DEVNULL, shell=shell),
    ]
    try:
        wait("http://127.0.0.1:8012/health")
        procs.append(subprocess.Popen(["node", "dist/index.js"], cwd=ROOT / "gateway", env=gw_env, shell=shell))
        wait("http://127.0.0.1:8092/healthz")
        sys.path.insert(0, str(ROOT / "control-plane"))
        from veyrqun import service

        service.reset_runtime_state()
        _, _, creds = service.create_agent({
            "agent_id": "load_agent", "display_name": "Load test agent",
            "allowed_actions": ["llm.chat", "waive_fee"], "blocked_actions": [],
            "spend_cap_daily": Decimal("100000000"), "redact_data_classes": ["email"],
        })
        token = httpx.post("http://127.0.0.1:8012/oauth/token", json={"grant_type": "client_credentials",
                           "client_id": creds["client_id"], "client_secret": creds["client_secret"]}).json()["access_token"]
        # warm-up: JWKS, bundle, JIT
        for _ in range(200):
            httpx.post("http://127.0.0.1:8092/mcp", headers={"authorization": f"Bearer {token}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "waive_fee", "arguments": {"amount": "0.01"}}})
        host = "host.docker.internal" if platform.system() in ("Windows", "Darwin") else "127.0.0.1"
        out = ROOT / "loadtest" / "k6-summary.json"
        cmd = ["docker", "run", "--rm", "-v", f"{ROOT / 'loadtest'}:/scripts"]
        if host == "127.0.0.1":
            cmd += ["--network", "host"]
        cmd += [K6_IMAGE, "run", "--quiet", "--summary-export=/scripts/k6-summary.json",
                "-e", f"GATEWAY=http://{host}:8092", "-e", "TOKEN", "-e", f"RATE={a.rate}",
                "-e", f"DURATION={a.duration}", "/scripts/gateway.js"]
        # The agent token goes through the environment (`-e TOKEN`), not argv, so it isn't in the process list.
        # Local developer tool with fixed arguments.
        k6_env = {**os.environ, "TOKEN": token}
        k6 = subprocess.run(cmd, capture_output=True, text=True, env=k6_env)  # nosemgrep: dangerous-subprocess-use-tainted-env-args
        summary = json.loads(out.read_text())
    finally:
        for p in procs:
            if shell:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()

    m = summary["metrics"]

    def trend(name: str) -> dict:
        v = m[name]
        return {k: round(v[k], 3) for k in ("med", "p(90)", "p(95)", "p(99)", "max", "avg") if k in v}

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "host": {"os": platform.platform(), "cpu_count": os.cpu_count()},
        "setup": {"rate_rps_tool_calls": a.rate, "rate_rps_chat": max(1, a.rate // 4), "duration": a.duration,
                  "upstreams": "local deterministic mocks", "redis": "docker (local)", "gateway": "node dist/index.js",
                  "k6": K6_IMAGE},
        "gateway_overhead_ms": trend("gateway_overhead_ms"),
        "upstream_ms": trend("upstream_ms"),
        "http_req_duration_ms": trend("http_req_duration"),
        "requests": int(m["http_reqs"]["count"]),
        "checks_pass_rate": round(m["checks"]["value"], 4),
        "target": "gateway overhead p99 < 10 ms",
        "threshold_passed": k6.returncode == 0,
    }
    (ROOT / "docs" / "results" / "gateway_latency.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if k6.returncode not in (0, 99):
        print(k6.stdout[-2000:], k6.stderr[-2000:])


if __name__ == "__main__":
    main()
