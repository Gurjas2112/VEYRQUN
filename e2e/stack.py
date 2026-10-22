"""Boot a throwaway local stack for integration runs: control plane (uvicorn), gateway (tsx) and the mock upstreams,
against a fresh Postgres database and a flushed Redis db. Used by e2e tests and by scripts that measure things.

Prereqs: docker compose up -d postgres redis ; npm --prefix gateway ci
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
OWNER = "postgresql+psycopg://veyrqun_owner:veyrqun_owner_dev@localhost:55432"
APP = "postgresql+psycopg://veyrqun_app:veyrqun_app_dev@localhost:55432"
NPX = shutil.which("npx") or "npx"


@dataclass
class Stack:
    cp: str
    gw: str
    db: str
    redis_url: str
    service: Any  # veyrqun.service, bound to this stack's database

    def owner_url(self) -> str:
        return f"{OWNER}/{self.db}"


def recreate_database(name: str, grant_app: bool = True) -> None:
    """DROP + CREATE a scratch database. Identifiers can't be bound parameters, so they are validated and quoted
    with psycopg.sql.Identifier instead of being formatted into SQL text."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError(f"bad database name {name!r}")
    db = sql.Identifier(name)
    with psycopg.connect(f"{OWNER.replace('+psycopg', '')}/postgres", autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(db))
        c.execute(sql.SQL("CREATE DATABASE {}").format(db))
        if grant_app:
            c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO veyrqun_app").format(db))


def wait(url: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up")


def _kill(p: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()


@contextmanager
def local_stack(*, name: str, db: str, redis_db: int, cp_port: int, gw_port: int, llm_port: int, mcp_port: int,
                service_token: str, extra_env: dict[str, str] | None = None) -> Iterator[Stack]:
    import redis

    recreate_database(db)
    redis_url = f"redis://localhost:56379/{redis_db}"
    redis.Redis.from_url(redis_url).flushdb()  # no stream events or counters from earlier runs
    cp, gw = f"http://127.0.0.1:{cp_port}", f"http://127.0.0.1:{gw_port}"
    cp_env = {**os.environ, "VEYRQUN_ENV": "dev", "VEYRQUN_SIMULATOR": "0", "DEFAULT_WORKSPACE_ENV": "staging",
              "DATABASE_URL": f"{APP}/{db}", "MIGRATION_DATABASE_URL": f"{OWNER}/{db}", "REDIS_URL": redis_url,
              "BUNDLE_SERVICE_TOKEN": service_token, "LOG_LEVEL": "WARNING",
              "GATEWAY_URL": f"http://127.0.0.1:{gw_port}", **(extra_env or {})}
    os.environ.update({k: v for k, v in cp_env.items() if k != "LOG_LEVEL"})  # this process talks to the same DB
    # Several stacks can run in one pytest process: drop settings and Lua scripts cached for the previous one.
    if (cfg := sys.modules.get("veyrqun.config")) is not None:
        cfg.get_settings.cache_clear()
    if (ctr := sys.modules.get("veyrqun.counters")) is not None:
        ctr._scripts.cache_clear()
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "control-plane", env=cp_env,
                   check=True, capture_output=True)
    gw_env = {**os.environ, "NODE_ENV": "test", "LOG_LEVEL": "warn", "PORT": str(gw_port), "HOST": "127.0.0.1",
              "CONTROL_PLANE_URL": cp, "GATEWAY_SERVICE_TOKEN": service_token, "REDIS_URL": redis_url,
              "UPSTREAM_LLM_URLS": f"http://127.0.0.1:{llm_port}", "UPSTREAM_MCP_URL": f"http://127.0.0.1:{mcp_port}/mcp"}
    logs = ROOT / "e2e"
    shell = os.name == "nt"
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(cp_port), "--log-level", "warning"],
                         cwd=ROOT / "control-plane", env=cp_env, stdout=open(logs / f"{name}-cp.log", "w"),
                         stderr=subprocess.STDOUT),
        subprocess.Popen([NPX, "tsx", "src/mock/llm.ts"], cwd=ROOT / "gateway", env={**os.environ, "PORT": str(llm_port)},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=shell),
        subprocess.Popen([NPX, "tsx", "src/mock/bankcore-mcp.ts"], cwd=ROOT / "gateway",
                         env={**os.environ, "PORT": str(mcp_port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         shell=shell),
    ]
    try:
        wait(f"{cp}/health")
        procs.append(subprocess.Popen([NPX, "tsx", "src/index.ts"], cwd=ROOT / "gateway", env=gw_env,
                                      stdout=open(logs / f"{name}-gw.log", "w"), stderr=subprocess.STDOUT, shell=shell))
        wait(f"{gw}/healthz")
        wait(f"http://127.0.0.1:{llm_port}/v1/chat/completions")
        if str(ROOT / "control-plane") not in sys.path:
            sys.path.insert(0, str(ROOT / "control-plane"))
        from veyrqun import service

        service.reset_runtime_state()
        yield Stack(cp=cp, gw=gw, db=db, redis_url=redis_url, service=service)
    finally:
        for p in procs:
            _kill(p)


def fleet_env(st: Stack):
    """An agents/fleet Env bound to a local stack: agents registered through the control plane, a human approver and
    an operator acting through its service layer, LangGraph checkpoints in their own Postgres database."""
    if str(ROOT / "agents") not in sys.path:
        sys.path.insert(0, str(ROOT / "agents"))
    from fleet.scenarios import Env
    from fleet.specs import registration
    from langgraph.checkpoint.postgres import PostgresSaver

    ckpt_db = f"{st.db}_ckpt"
    recreate_database(ckpt_db, grant_app=False)
    ckpt_url = f"{OWNER.replace('+psycopg', '')}/{ckpt_db}"
    with PostgresSaver.from_conn_string(ckpt_url) as saver:
        saver.setup()

    def register(agent_id: str) -> dict:
        _, _, creds = st.service.create_agent(registration(agent_id), actor="user:fleet-admin")
        return creds

    def operator(action: str, agent_id: str) -> None:
        if action != "stop_agent":
            raise ValueError(action)
        st.service.stop_agent(agent_id, reason="scenario: operator kill switch", actor="user:fleet-operator")
        # Like an operator watching the dashboard: wait until the gateway enforces the new policy revision.
        from veyrqun import counters

        target, ws = counters.bundle_revision(), str(st.service.default_workspace())
        deadline = time.time() + 10
        while time.time() < deadline:
            rev = httpx.get(f"{st.gw}/readyz", timeout=2).json().get("bundle_revisions", {}).get(ws)
            if rev is not None and target is not None and rev >= target:
                return
            time.sleep(0.05)
        raise RuntimeError("gateway did not pick up the agent stop within 10 s")

    return Env(
        gateway_url=st.gw, control_plane_url=st.cp, register=register,
        decide=lambda approval_id, approve, reason: st.service.decide_approval(
            approval_id, approve, actor="user:fleet-approver", reason=reason),
        operator=operator,
        checkpointer=lambda: PostgresSaver.from_conn_string(ckpt_url),
    )
