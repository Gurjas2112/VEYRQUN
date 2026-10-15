"""Governance service layer: every state change goes through here, under one lock,
and leaves a hash-chained audit entry behind."""

import json
import sqlite3
import time
from collections import defaultdict, deque
from typing import Optional

from . import audit
from .db import get_db, insert_agent, insert_policy_version, row_to_agent, row_to_policy_version, write_lock
from .metrics import metrics
from .policy_engine import APPROVED, evaluate_policy

FLEET_AGENT_ID = "*fleet*"
OPERATOR_OUTCOME = "executed"
POLICY_FIELDS = (
    "allowed_actions",
    "blocked_actions",
    "spend_cap_daily",
    "max_per_transaction",
    "rate_limit_per_minute",
    "action_limits",
)


class GovernanceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# Sliding 60s windows of approved-action timestamps per agent, for rate limiting.
_rate_windows: dict[str, deque] = defaultdict(deque)


def reset_runtime_state() -> None:
    _rate_windows.clear()
    metrics.reset()


def _recent_count(agent_id: str, now: float) -> int:
    window = _rate_windows[agent_id]
    while window and now - window[0] >= 60.0:
        window.popleft()
    return len(window)


def _fetch_agent(conn: sqlite3.Connection, agent_id: str) -> dict:
    row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    if not row:
        raise GovernanceError(404, f"Agent '{agent_id}' not found")
    return row_to_agent(row)


def _all_agents(conn: sqlite3.Connection) -> list[dict]:
    return [row_to_agent(r) for r in conn.execute("SELECT * FROM agents ORDER BY rowid")]


def _fleet_stopped(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM fleet_state WHERE key = 'fleet_stopped'").fetchone()
    return bool(row and row["value"] == "1")


def _operator_entry(conn: sqlite3.Connection, agent_id: str, event: str, policy_version: int = 0) -> dict:
    metrics.observe_operator_event(event)
    return audit.append_entry(conn, agent_id, f"operator:{event}", 0.0, OPERATOR_OUTCOME, None, policy_version)


# ---------------------------------------------------------------- reads


def list_agents() -> list[dict]:
    conn = get_db()
    try:
        return _all_agents(conn)
    finally:
        conn.close()


def get_agent(agent_id: str) -> dict:
    conn = get_db()
    try:
        return _fetch_agent(conn, agent_id)
    finally:
        conn.close()


def fleet_status() -> dict:
    conn = get_db()
    try:
        by_status = {"active": 0, "stopped": 0, "revoked": 0}
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM agents GROUP BY status"):
            by_status[r["status"]] = r["n"]
        return {"fleet_stopped": _fleet_stopped(conn), "agents_by_status": by_status}
    finally:
        conn.close()


def policy_history(agent_id: str) -> list[dict]:
    conn = get_db()
    try:
        _fetch_agent(conn, agent_id)
        rows = conn.execute(
            "SELECT * FROM policy_versions WHERE agent_id = ? ORDER BY version DESC", (agent_id,)
        ).fetchall()
        return [row_to_policy_version(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------- data plane


def attempt_action(agent_id: str, action_type: str, amount: float) -> tuple[dict, dict]:
    """Evaluate one agent action against live policy; approved spend is committed atomically."""
    started = time.perf_counter_ns()
    with write_lock:
        conn = get_db()
        try:
            agent = _fetch_agent(conn, agent_id)
            now = time.monotonic()
            eval_started = time.perf_counter_ns()
            outcome, rule = evaluate_policy(
                agent,
                action_type,
                amount,
                fleet_stopped=_fleet_stopped(conn),
                recent_actions=_recent_count(agent_id, now),
            )
            eval_ns = time.perf_counter_ns() - eval_started
            if outcome == APPROVED:
                agent["spend_used_today"] = round(agent["spend_used_today"] + amount, 2)
                conn.execute(
                    "UPDATE agents SET spend_used_today = ? WHERE agent_id = ?",
                    (agent["spend_used_today"], agent_id),
                )
                _rate_windows[agent_id].append(now)
            entry = audit.append_entry(conn, agent_id, action_type, amount, outcome, rule, agent["policy_version"])
            conn.commit()
        finally:
            conn.close()
    metrics.observe_decision(agent_id, outcome, rule, eval_ns, time.perf_counter_ns() - started)
    return entry, agent


# ---------------------------------------------------------------- control plane


def create_agent(agent: dict) -> tuple[dict, dict]:
    with write_lock:
        conn = get_db()
        try:
            if conn.execute("SELECT 1 FROM agents WHERE agent_id = ?", (agent["agent_id"],)).fetchone():
                raise GovernanceError(409, "Agent ID already exists")
            insert_agent(conn, agent)
            entry = _operator_entry(conn, agent["agent_id"], "register_agent", 1)
            conn.commit()
            return entry, _fetch_agent(conn, agent["agent_id"])
        finally:
            conn.close()


def _apply_policy(conn: sqlite3.Connection, agent_id: str, policy: dict, new_version: int) -> None:
    conn.execute(
        """
        UPDATE agents
        SET allowed_actions = ?, blocked_actions = ?, spend_cap_daily = ?, max_per_transaction = ?,
            rate_limit_per_minute = ?, action_limits = ?, policy_version = ?
        WHERE agent_id = ?
        """,
        (
            json.dumps(policy["allowed_actions"]),
            json.dumps(policy["blocked_actions"]),
            policy["spend_cap_daily"],
            policy["max_per_transaction"],
            policy["rate_limit_per_minute"],
            json.dumps(policy["action_limits"] or {}),
            new_version,
            agent_id,
        ),
    )


def update_policy(agent_id: str, changes: dict, note: Optional[str] = None) -> tuple[dict, dict]:
    """`changes` holds only the fields the caller explicitly set (None clears an optional limit)."""
    with write_lock:
        conn = get_db()
        try:
            agent = _fetch_agent(conn, agent_id)
            policy = {field: agent[field] for field in POLICY_FIELDS}
            for field, value in changes.items():
                if field not in POLICY_FIELDS:
                    continue
                if value is None and field in ("allowed_actions", "blocked_actions", "spend_cap_daily"):
                    continue
                policy[field] = value
            new_version = agent["policy_version"] + 1
            _apply_policy(conn, agent_id, policy, new_version)
            insert_policy_version(conn, agent_id, new_version, policy, note or "policy update")
            entry = _operator_entry(conn, agent_id, "policy_update", new_version)
            conn.commit()
            return entry, _fetch_agent(conn, agent_id)
        finally:
            conn.close()


def rollback_policy(agent_id: str, version: int) -> tuple[dict, dict]:
    """Rollback is forward-only: it re-publishes an old version's rules as a new version."""
    with write_lock:
        conn = get_db()
        try:
            agent = _fetch_agent(conn, agent_id)
            row = conn.execute(
                "SELECT * FROM policy_versions WHERE agent_id = ? AND version = ?", (agent_id, version)
            ).fetchone()
            if not row:
                raise GovernanceError(404, f"Policy version {version} not found for '{agent_id}'")
            policy = row_to_policy_version(row)
            new_version = agent["policy_version"] + 1
            _apply_policy(conn, agent_id, policy, new_version)
            insert_policy_version(conn, agent_id, new_version, policy, f"rollback to v{version}")
            entry = _operator_entry(conn, agent_id, f"policy_rollback_v{version}", new_version)
            conn.commit()
            return entry, _fetch_agent(conn, agent_id)
        finally:
            conn.close()


def _set_status(agent_id: str, new_status: str, event: str, allowed_from: tuple, reason: Optional[str]) -> tuple[dict, dict]:
    with write_lock:
        conn = get_db()
        try:
            agent = _fetch_agent(conn, agent_id)
            if agent["status"] not in allowed_from:
                raise GovernanceError(409, f"Agent '{agent_id}' is {agent['status']}; cannot {event}")
            conn.execute(
                "UPDATE agents SET status = ?, status_reason = ? WHERE agent_id = ?",
                (new_status, reason, agent_id),
            )
            entry = _operator_entry(conn, agent_id, event, agent["policy_version"])
            conn.commit()
            return entry, _fetch_agent(conn, agent_id)
        finally:
            conn.close()


def stop_agent(agent_id: str, reason: Optional[str] = None) -> tuple[dict, dict]:
    return _set_status(agent_id, "stopped", "emergency_stop", ("active", "stopped"), reason or "operator emergency stop")


def resume_agent(agent_id: str) -> tuple[dict, dict]:
    return _set_status(agent_id, "active", "resume", ("active", "stopped"), None)


def revoke_agent(agent_id: str, reason: Optional[str] = None) -> tuple[dict, dict]:
    """Revocation outlives fleet resume: only an explicit reinstate brings the agent back."""
    return _set_status(agent_id, "revoked", "revoke", ("active", "stopped", "revoked"), reason or "credentials revoked")


def reinstate_agent(agent_id: str) -> tuple[dict, dict]:
    return _set_status(agent_id, "active", "reinstate", ("revoked",), None)


def fleet_stop() -> tuple[dict, list[dict]]:
    with write_lock:
        conn = get_db()
        try:
            conn.execute("UPDATE fleet_state SET value = '1' WHERE key = 'fleet_stopped'")
            conn.execute(
                "UPDATE agents SET status = 'stopped', status_reason = 'fleet emergency stop' WHERE status = 'active'"
            )
            entry = _operator_entry(conn, FLEET_AGENT_ID, "fleet_emergency_stop")
            conn.commit()
            return entry, _all_agents(conn)
        finally:
            conn.close()


def fleet_resume() -> tuple[dict, list[dict]]:
    with write_lock:
        conn = get_db()
        try:
            conn.execute("UPDATE fleet_state SET value = '0' WHERE key = 'fleet_stopped'")
            conn.execute("UPDATE agents SET status = 'active', status_reason = NULL WHERE status = 'stopped'")
            entry = _operator_entry(conn, FLEET_AGENT_ID, "fleet_resume")
            conn.commit()
            return entry, _all_agents(conn)
        finally:
            conn.close()


def reset_spend(agent_id: str) -> tuple[dict, dict]:
    with write_lock:
        conn = get_db()
        try:
            agent = _fetch_agent(conn, agent_id)
            conn.execute("UPDATE agents SET spend_used_today = 0.0 WHERE agent_id = ?", (agent_id,))
            entry = _operator_entry(conn, agent_id, "reset_spend", agent["policy_version"])
            conn.commit()
            return entry, _fetch_agent(conn, agent_id)
        finally:
            conn.close()


def reset_all_spend() -> tuple[dict, list[dict]]:
    with write_lock:
        conn = get_db()
        try:
            conn.execute("UPDATE agents SET spend_used_today = 0.0")
            entry = _operator_entry(conn, FLEET_AGENT_ID, "reset_all_spend")
            conn.commit()
            return entry, _all_agents(conn)
        finally:
            conn.close()
