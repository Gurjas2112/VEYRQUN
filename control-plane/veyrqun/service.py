"""Governance service layer. Every decision and every operator action goes through here and
leaves a hash-chained audit entry. Decisions: OPA-WASM policy -> Redis Lua counters -> ledger."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import audit, counters, keys
from .config import get_settings
from .db import PLATFORM, bump_bundle_revision, ensure_seed, insert_agent, insert_policy_version, tx, utcnow
from .ids import uuid7
from .metrics import metrics
from .models import check_policy_rules
from .money import MoneyError, fmt, from_minor, to_decimal, to_minor
from .policy import engine
from .policy.store import store

FLEET_AGENT_ID = "*fleet*"
OPERATOR_OUTCOME = "executed"
POLICY_FIELDS = (
    "allowed_actions", "blocked_actions", "spend_cap_daily", "max_per_transaction", "rate_limit_per_minute",
    "action_limits", "action_daily_caps", "approval_threshold", "redact_data_classes", "block_data_classes",
)
NON_NULLABLE = {"allowed_actions", "blocked_actions", "spend_cap_daily"}
APPROVAL_TTL = timedelta(hours=24)


class GovernanceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@lru_cache(maxsize=1)
def default_workspace() -> UUID:
    return ensure_seed()


def reset_runtime_state() -> None:
    metrics.reset()
    store.invalidate()
    engine.reset()
    keys.reset_cache()
    default_workspace.cache_clear()


# ---------------------------------------------------------------- views


def _policy_dict(row) -> dict:
    return {
        "allowed_actions": list(row.allowed_actions),
        "blocked_actions": list(row.blocked_actions),
        "spend_cap_daily": row.spend_cap_daily,
        "max_per_transaction": row.max_per_transaction,
        "rate_limit_per_minute": row.rate_limit_per_minute,
        "action_limits": {k: Decimal(str(v)) for k, v in (row.action_limits or {}).items()},
        "action_daily_caps": {k: Decimal(str(v)) for k, v in (row.action_daily_caps or {}).items()},
        "approval_threshold": row.approval_threshold,
        "redact_data_classes": list(row.redact_data_classes),
        "block_data_classes": list(row.block_data_classes),
    }


def _money_map(m: dict) -> dict:
    return {k: fmt(v) for k, v in m.items()}


def agent_view(row, workspace_id: UUID) -> dict:
    policy = _policy_dict(row)
    try:
        spent = from_minor(counters.spend_today_minor(str(workspace_id), row.agent_key, row.timezone), row.currency)
        spent_str: str | None = fmt(spent)
    except counters.CounterUnavailable:
        spent_str = None  # unknown, not zero: the UI shows "state store unavailable"
    return {
        "agent_id": row.agent_key,
        "display_name": row.display_name,
        "status": row.status,
        "status_reason": row.status_reason,
        "mode": row.mode,
        "timezone": row.timezone,
        "currency": row.currency,
        "accountable_owner": row.accountable_owner,
        "business_service": row.business_service,
        "owner_user_id": str(row.owner_user_id) if row.owner_user_id else None,
        "policy_version": row.policy_version,
        "allowed_actions": policy["allowed_actions"],
        "blocked_actions": policy["blocked_actions"],
        "spend_cap_daily": fmt(policy["spend_cap_daily"]),
        "spend_used_today": spent_str,
        "max_per_transaction": fmt(policy["max_per_transaction"]),
        "rate_limit_per_minute": policy["rate_limit_per_minute"],
        "action_limits": _money_map(policy["action_limits"]),
        "action_daily_caps": _money_map(policy["action_daily_caps"]),
        "approval_threshold": fmt(policy["approval_threshold"]),
        "redact_data_classes": policy["redact_data_classes"],
        "block_data_classes": policy["block_data_classes"],
    }


def _snapshot(workspace_id: UUID):
    return store.get(workspace_id, counters.bundle_revision())


def list_agents(workspace_id: UUID | None = None) -> list[dict]:
    ws = workspace_id or default_workspace()
    _, _, rows = _snapshot(ws)
    return [agent_view(r, ws) for r in rows]


def _find_row(workspace_id: UUID, agent_key: str):
    _, _, rows = _snapshot(workspace_id)
    for r in rows:
        if r.agent_key == agent_key:
            return r
    raise GovernanceError(404, f"Agent '{agent_key}' not found")


def get_agent(agent_key: str, workspace_id: UUID | None = None) -> dict:
    ws = workspace_id or default_workspace()
    return agent_view(_find_row(ws, agent_key), ws)


def fleet_status(workspace_id: UUID | None = None) -> dict:
    ws = workspace_id or default_workspace()
    with tx(ws) as conn:
        by_status = {"active": 0, "stopped": 0, "revoked": 0}
        for r in conn.execute(text("SELECT status, count(*) AS n FROM gov.agents WHERE workspace_id = :w GROUP BY status"),
                              {"w": ws}):
            by_status[r.status] = r.n
        fleet = conn.execute(text("SELECT stopped, version FROM gov.fleet_state WHERE workspace_id = :w"), {"w": ws}).first()
    return {"fleet_stopped": bool(fleet and fleet.stopped), "fleet_version": fleet.version if fleet else 0,
            "agents_by_status": by_status}


def policy_history(agent_key: str, workspace_id: UUID | None = None) -> list[dict]:
    ws = workspace_id or default_workspace()
    row = _find_row(ws, agent_key)
    with tx(ws) as conn:
        rows = conn.execute(text("SELECT * FROM gov.policy_versions WHERE agent_id = :a ORDER BY version DESC"),
                            {"a": row.id}).all()
    out = []
    for r in rows:
        p = _policy_dict(r)
        out.append({
            "policy_id": str(r.id), "agent_id": agent_key, "version": r.version,
            "created_at": r.created_at.isoformat(), "created_by": r.created_by, "note": r.note,
            **{k: (fmt(v) if isinstance(v, Decimal) else (_money_map(v) if k in ("action_limits", "action_daily_caps") else v))
               for k, v in p.items()},
        })
    return out


# ---------------------------------------------------------------- data plane (legacy path)


def _check_approval(conn, approval_id: str, agent_row, action: str, amount: Decimal) -> UUID:
    row = conn.execute(text("SELECT * FROM gov.approvals WHERE id = :id FOR UPDATE"), {"id": approval_id}).first()
    if row is None or row.agent_id != agent_row.id or row.kind != "action":
        raise GovernanceError(404, "approval not found for this agent")
    if row.status != "approved":
        raise GovernanceError(409, f"approval is {row.status}")
    if row.expires_at < utcnow():
        raise GovernanceError(409, "approval expired")
    if row.action_type != action or row.amount != amount:
        raise GovernanceError(409, "approval does not match this action/amount")
    return row.id


def attempt_action(
    agent_key: str,
    action_type: str,
    amount: Decimal | str | int,
    *,
    actor: str | None = None,
    data_classes: list[str] | None = None,
    signals: dict | None = None,
    approval_id: str | None = None,
    workspace_id: UUID | None = None,
) -> tuple[dict, dict | None, dict]:
    """Returns (ledger entry, agent view or None, decision details)."""
    started = time.perf_counter_ns()
    ws = workspace_id or default_workspace()
    revision, data, rows = _snapshot(ws)
    agent_doc = data["agents"].get(agent_key)
    row = next((r for r in rows if r.agent_key == agent_key), None)
    currency = agent_doc["currency"] if agent_doc else "USD"
    tz = agent_doc["timezone"] if agent_doc else "UTC"
    try:
        amount_dec = to_decimal(amount, currency)
    except MoneyError as exc:
        raise GovernanceError(422, str(exc)) from exc
    amount_minor = to_minor(amount_dec, currency)

    approval_granted = False
    if approval_id:
        if row is None:
            raise GovernanceError(404, f"Agent '{agent_key}' not found")
        with tx(ws) as conn:
            _check_approval(conn, approval_id, row, action_type, amount_dec)
        approval_granted = True

    input_doc = {
        "agent_id": agent_key,
        "action": action_type,
        "amount_minor": amount_minor,
        "data_classes": sorted(set(data_classes or [])),
        "signals": signals or {},
        "approval_granted": approval_granted,
    }
    eval_started = time.perf_counter_ns()
    decision = engine.evaluate(ws, revision, data, input_doc)
    eval_ns = time.perf_counter_ns() - eval_started

    outcome, rule = decision["outcome"], decision["rule_id"]
    mode = decision.get("mode", "enforce")
    reservation = None
    if outcome in ("allow", "redact"):
        try:
            ok, counter_rule, reservation = counters.reserve(str(ws), agent_key, action_type, tz, amount_minor,
                                                              decision.get("limits") or {})
            if not ok:
                outcome, rule, reservation = "block", counter_rule, None
        except counters.CounterUnavailable:
            outcome, rule = "block", "state_store_unavailable"  # fail closed: spend/rate state unknown

    approval_new = None
    try:
        with tx(ws) as conn:
            if outcome == "require_approval" and row is not None:
                approval_new = uuid7()
                conn.execute(
                    text("INSERT INTO gov.approvals (id, workspace_id, agent_id, kind, action_type, amount, currency, "
                         "requested_by, expires_at) VALUES (:id, :ws, :a, 'action', :act, :amt, :cur, :by, :exp)"),
                    {"id": approval_new, "ws": ws, "a": row.id, "act": action_type, "amt": amount_dec, "cur": currency,
                     "by": f"agent:{agent_key}", "exp": utcnow() + APPROVAL_TTL},
                )
            entry = audit.append(
                conn, workspace_id=ws, actor=actor or f"agent:{agent_key}", agent_id=agent_key,
                action_type=action_type, amount=amount_dec, currency=currency, outcome=outcome, mode=mode,
                rule_id=rule, policy_version=decision.get("policy_version", 0),
            )
            if approval_granted and outcome in ("allow", "redact"):
                conn.execute(text("UPDATE gov.approvals SET status = 'consumed', decision_id = :d WHERE id = :id"),
                             {"d": entry["decision_id"], "id": approval_id})
    except Exception:
        if reservation:
            counters.release(str(ws), agent_key, action_type, tz, amount_minor, reservation)
        raise

    enforced = mode == "enforce"
    effective = outcome if enforced else ("allow" if outcome in ("block", "require_approval") else outcome)
    metrics.observe_decision(agent_key, outcome, rule, eval_ns, time.perf_counter_ns() - started)
    details = {
        "effective_outcome": effective,
        "enforced": enforced,
        "redactions": decision.get("redactions", []) if outcome in ("redact", "require_approval") else [],
        "approval_id": str(approval_new) if approval_new else None,
    }
    view = agent_view(row, ws) if row is not None else None
    return entry, view, details


# ---------------------------------------------------------------- control plane


def _operator_entry(conn, ws: UUID, actor: str, agent_id: str, event: str, policy_version: int = 0) -> dict:
    metrics.observe_operator_event(event)
    return audit.append(conn, workspace_id=ws, actor=actor, agent_id=agent_id, action_type=f"operator:{event}",
                        amount=Decimal("0"), currency="USD", outcome=OPERATOR_OUTCOME, rule_id=None,
                        policy_version=policy_version)


def _changed(conn, reason: str) -> int:
    revision = bump_bundle_revision(conn, reason)
    return revision


def _after_commit(revision: int) -> None:
    store.invalidate()
    counters.publish_revision(revision)


def create_agent(payload: dict, actor: str = "operator", workspace_id: UUID | None = None) -> tuple[dict, dict, dict]:
    ws = workspace_id or default_workspace()
    secret, secret_hash = keys.new_client_secret()
    agent = {**payload, "agent_key": payload["agent_id"], "client_secret_hash": secret_hash}
    try:
        with tx(ws) as conn:
            insert_agent(conn, ws, agent, actor)
            entry = _operator_entry(conn, ws, actor, agent["agent_key"], "register_agent", 1)
            revision = _changed(conn, f"register {agent['agent_key']}")
    except IntegrityError as exc:
        if "agents_workspace_id_agent_key_key" in str(exc.orig):
            raise GovernanceError(409, "Agent ID already exists") from exc
        raise GovernanceError(422, "agent violates a policy constraint") from exc
    _after_commit(revision)
    creds = {"client_id": f"{_ws_slug(ws)}:{agent['agent_key']}", "client_secret": secret,
             "note": "store the secret now; it is not shown again"}
    return entry, get_agent(agent["agent_key"], ws), creds


def _ws_slug(ws: UUID) -> str:
    with tx() as conn:
        return conn.execute(text("SELECT slug FROM iam.workspaces WHERE id = :w"), {"w": ws}).scalar_one()


def update_policy(agent_key: str, changes: dict, note: str | None = None, actor: str = "operator",
                  workspace_id: UUID | None = None) -> tuple[dict, dict]:
    ws = workspace_id or default_workspace()
    row = _find_row(ws, agent_key)
    policy = _policy_dict(row)
    for field, value in changes.items():
        if field not in POLICY_FIELDS or (value is None and field in NON_NULLABLE):
            continue
        policy[field] = value if value is not None or field not in ("action_limits", "action_daily_caps") else {}
    try:
        check_policy_rules(policy)
    except ValueError as exc:
        raise GovernanceError(422, str(exc)) from exc
    new_version = row.policy_version + 1
    with tx(ws) as conn:
        current = conn.execute(text("SELECT policy_version FROM gov.agents WHERE id = :a FOR UPDATE"), {"a": row.id}).scalar_one()
        if current != row.policy_version:
            raise GovernanceError(409, "policy changed concurrently; reload and retry")
        insert_policy_version(conn, row.id, new_version, policy, actor, note or "policy update")
        conn.execute(text("UPDATE gov.agents SET policy_version = :v, updated_at = now() WHERE id = :a"),
                     {"v": new_version, "a": row.id})
        entry = _operator_entry(conn, ws, actor, agent_key, "policy_update", new_version)
        revision = _changed(conn, f"policy {agent_key} v{new_version}")
    _after_commit(revision)
    return entry, get_agent(agent_key, ws)


def rollback_policy(agent_key: str, version: int, actor: str = "operator", workspace_id: UUID | None = None,
                    note: str | None = None) -> tuple[dict, dict]:
    """Forward-only: re-publishes an old version's rules as a new version."""
    ws = workspace_id or default_workspace()
    row = _find_row(ws, agent_key)
    with tx(ws) as conn:
        old = conn.execute(text("SELECT * FROM gov.policy_versions WHERE agent_id = :a AND version = :v"),
                           {"a": row.id, "v": version}).first()
        if old is None:
            raise GovernanceError(404, f"Policy version {version} not found for '{agent_key}'")
        current = conn.execute(text("SELECT policy_version FROM gov.agents WHERE id = :a FOR UPDATE"), {"a": row.id}).scalar_one()
        new_version = current + 1
        insert_policy_version(conn, row.id, new_version, _policy_dict(old), actor, note or f"rollback to v{version}")
        conn.execute(text("UPDATE gov.agents SET policy_version = :v, updated_at = now() WHERE id = :a"),
                     {"v": new_version, "a": row.id})
        entry = _operator_entry(conn, ws, actor, agent_key, f"policy_rollback_v{version}", new_version)
        revision = _changed(conn, f"rollback {agent_key} to v{version}")
    _after_commit(revision)
    return entry, get_agent(agent_key, ws)


def _set_status(agent_key: str, new_status: str, event: str, allowed_from: tuple, reason: str | None,
                actor: str, workspace_id: UUID | None) -> tuple[dict, dict]:
    ws = workspace_id or default_workspace()
    row = _find_row(ws, agent_key)
    with tx(ws) as conn:
        status = conn.execute(text("SELECT status FROM gov.agents WHERE id = :a FOR UPDATE"), {"a": row.id}).scalar_one()
        if status not in allowed_from:
            raise GovernanceError(409, f"Agent '{agent_key}' is {status}; cannot {event}")
        conn.execute(text("UPDATE gov.agents SET status = :s, status_reason = :r, updated_at = now() WHERE id = :a"),
                     {"s": new_status, "r": reason, "a": row.id})
        entry = _operator_entry(conn, ws, actor, agent_key, event, row.policy_version)
        revision = _changed(conn, f"{event} {agent_key}")
    _after_commit(revision)
    counters.publish_control(json.dumps({"type": "agent_status", "workspace": str(ws), "agent": agent_key,
                                         "status": new_status, "revision": revision}))
    return entry, get_agent(agent_key, ws)


def stop_agent(agent_key: str, reason: str | None = None, actor: str = "operator", workspace_id: UUID | None = None):
    return _set_status(agent_key, "stopped", "emergency_stop", ("active", "stopped"), reason or "operator emergency stop",
                       actor, workspace_id)


def resume_agent(agent_key: str, actor: str = "operator", workspace_id: UUID | None = None):
    return _set_status(agent_key, "active", "resume", ("active", "stopped"), None, actor, workspace_id)


def revoke_agent(agent_key: str, reason: str | None = None, actor: str = "operator", workspace_id: UUID | None = None):
    """Revocation outlives fleet resume: only an explicit reinstate brings the agent back."""
    result = _set_status(agent_key, "revoked", "revoke", ("active", "stopped", "revoked"), reason or "credentials revoked",
                         actor, workspace_id)
    ws = workspace_id or default_workspace()
    with tx(ws) as conn:
        conn.execute(text("UPDATE gov.agents SET client_secret_hash = NULL WHERE workspace_id = :w AND agent_key = :k"),
                     {"w": ws, "k": agent_key})
    return result


def reinstate_agent(agent_key: str, actor: str = "operator", workspace_id: UUID | None = None):
    return _set_status(agent_key, "active", "reinstate", ("revoked",), None, actor, workspace_id)


def _fleet(stopped: bool, actor: str, workspace_id: UUID | None) -> tuple[dict, list[dict]]:
    ws = workspace_id or default_workspace()
    with tx(ws) as conn:
        version = conn.execute(
            text("UPDATE gov.fleet_state SET stopped = :s, version = version + 1, updated_at = now(), updated_by = :by "
                 "WHERE workspace_id = :w RETURNING version"),
            {"s": stopped, "by": actor, "w": ws},
        ).scalar_one()
        if stopped:
            conn.execute(text("UPDATE gov.agents SET status = 'stopped', status_reason = 'fleet emergency stop' "
                              "WHERE workspace_id = :w AND status = 'active'"), {"w": ws})
        else:
            conn.execute(text("UPDATE gov.agents SET status = 'active', status_reason = NULL "
                              "WHERE workspace_id = :w AND status = 'stopped'"), {"w": ws})
        entry = _operator_entry(conn, ws, actor, FLEET_AGENT_ID, "fleet_emergency_stop" if stopped else "fleet_resume")
        revision = _changed(conn, "fleet stop" if stopped else "fleet resume")
    # Gateways act on this message immediately; the bundle revision is the durable backstop.
    counters.publish_control(json.dumps({"type": "fleet", "workspace": str(ws), "stopped": stopped,
                                         "version": version, "revision": revision}))
    _after_commit(revision)
    return entry, list_agents(ws)


def fleet_stop(actor: str = "operator", workspace_id: UUID | None = None):
    return _fleet(True, actor, workspace_id)


def fleet_resume(actor: str = "operator", workspace_id: UUID | None = None):
    return _fleet(False, actor, workspace_id)


def reset_spend(agent_key: str, actor: str = "operator", workspace_id: UUID | None = None) -> tuple[dict, dict]:
    ws = workspace_id or default_workspace()
    row = _find_row(ws, agent_key)
    counters.reset_spend(str(ws), agent_key, row.timezone)
    with tx(ws) as conn:
        entry = _operator_entry(conn, ws, actor, agent_key, "reset_spend", row.policy_version)
    return entry, get_agent(agent_key, ws)


def reset_all_spend(actor: str = "operator", workspace_id: UUID | None = None) -> tuple[dict, list[dict]]:
    ws = workspace_id or default_workspace()
    _, _, rows = _snapshot(ws)
    for r in rows:
        counters.reset_spend(str(ws), r.agent_key, r.timezone)
    with tx(ws) as conn:
        entry = _operator_entry(conn, ws, actor, FLEET_AGENT_ID, "reset_all_spend")
    return entry, list_agents(ws)


# ---------------------------------------------------------------- approvals


def _approval_view(r) -> dict:
    return {"approval_id": str(r.id), "kind": r.kind, "agent_id": r.agent_key, "action_type": r.action_type,
            "amount": fmt(r.amount), "currency": (r.currency or "").strip() or None, "status": r.status,
            "requested_by": r.requested_by, "decided_by": r.decided_by, "reason": r.reason,
            "created_at": r.created_at.isoformat(), "expires_at": r.expires_at.isoformat(),
            "decided_at": r.decided_at.isoformat() if r.decided_at else None, "payload": r.payload or {}}


def get_approval(approval_id: str, workspace_id: UUID) -> dict:
    with tx(workspace_id) as conn:
        row = conn.execute(text("SELECT p.*, a.agent_key FROM gov.approvals p LEFT JOIN gov.agents a ON a.id = p.agent_id "
                                "WHERE p.id = :id"), {"id": approval_id}).first()
    if row is None:
        raise GovernanceError(404, "approval not found")
    return _approval_view(row)


def list_approvals(status: str | None = "pending", workspace_id: UUID | None = None) -> list[dict]:
    ws = workspace_id or default_workspace()
    with tx(ws) as conn:
        conn.execute(text("UPDATE gov.approvals SET status = 'expired' WHERE workspace_id = :w AND status = 'pending' "
                          "AND expires_at < now()"), {"w": ws})
        rows = conn.execute(
            text("SELECT p.*, a.agent_key FROM gov.approvals p LEFT JOIN gov.agents a ON a.id = p.agent_id "
                 "WHERE p.workspace_id = :w AND (CAST(:s AS text) IS NULL OR p.status = :s) "
                 "ORDER BY p.created_at DESC LIMIT 200"),
            {"w": ws, "s": status},
        ).all()
    return [_approval_view(r) for r in rows]


def workspace_env(workspace_id: UUID) -> str:
    with tx() as conn:
        return conn.execute(text("SELECT environment FROM iam.workspaces WHERE id = :w"), {"w": workspace_id}).scalar_one()


def _jsonable(value):
    if isinstance(value, Decimal):
        return fmt(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def request_change(kind: str, payload: dict, actor: str, workspace_id: UUID, agent_key: str | None = None) -> dict:
    """Maker side of maker-checker: record the request; an approver (not the maker) executes it."""
    agent_id = _find_row(workspace_id, agent_key).id if agent_key else None
    approval_id = uuid7()
    with tx(workspace_id) as conn:
        conn.execute(
            text("INSERT INTO gov.approvals (id, workspace_id, agent_id, kind, payload, requested_by, expires_at) "
                 "VALUES (:id, :w, :a, :k, CAST(:p AS jsonb), :by, :exp)"),
            {"id": approval_id, "w": workspace_id, "a": agent_id, "k": kind, "p": json.dumps(_jsonable(payload)),
             "by": actor, "exp": utcnow() + APPROVAL_TTL},
        )
        _operator_entry(conn, workspace_id, actor, agent_key or FLEET_AGENT_ID, f"change_requested:{kind}")
        row = conn.execute(text("SELECT p.*, a.agent_key FROM gov.approvals p LEFT JOIN gov.agents a ON a.id = p.agent_id "
                                "WHERE p.id = :id"), {"id": approval_id}).first()
    return _approval_view(row)


def _execute_change(row, approver: str, ws: UUID) -> None:
    """Checker side: apply an approved change. The maker stays the recorded author of the change."""
    payload = row.payload or {}
    maker = row.requested_by
    if row.kind == "policy_publish":
        current = _find_row(ws, row.agent_key).policy_version
        if current != payload.get("base_version"):
            raise GovernanceError(409, f"policy changed since the request (v{payload.get('base_version')} -> v{current})")
        if payload.get("op") == "rollback":
            rollback_policy(row.agent_key, int(payload["version"]), actor=maker, workspace_id=ws,
                            note=f"rollback to v{payload['version']} (approved by {approver})")
        else:
            from .models import AgentPolicyUpdate

            update = AgentPolicyUpdate.model_validate(payload["changes"])
            changes = {f: getattr(update, f) for f in update.model_fields_set if f != "note"}
            update_policy(row.agent_key, changes, f"{payload.get('note') or 'policy update'} (approved by {approver})",
                          actor=maker, workspace_id=ws)
    elif row.kind == "kill_release":
        fleet_resume(actor=maker, workspace_id=ws)
    elif row.kind == "role_grant":
        from .auth.directory import grant_role

        grant_role(ws, UUID(payload["user_id"]), payload["role"], f"{maker} (approved by {approver})")


def decide_approval(approval_id: str, approve: bool, actor: str, reason: str | None = None,
                    workspace_id: UUID | None = None) -> tuple[dict, dict]:
    ws = workspace_id or default_workspace()
    with tx(ws) as conn:
        row = conn.execute(text("SELECT p.*, a.agent_key FROM gov.approvals p LEFT JOIN gov.agents a ON a.id = p.agent_id "
                                "WHERE p.id = :id AND p.workspace_id = :w FOR UPDATE OF p"), {"id": approval_id, "w": ws}).first()
        if row is None:
            raise GovernanceError(404, "approval not found")
        if row.status != "pending":
            raise GovernanceError(409, f"approval is already {row.status}")
        if row.expires_at < utcnow():
            raise GovernanceError(409, "approval expired")
        if row.requested_by == actor:
            raise GovernanceError(403, "maker-checker: you cannot decide a request you raised")
    if approve and row.kind != "action":
        try:
            _execute_change(row, actor, ws)
        except GovernanceError as exc:
            with tx(ws) as conn:
                conn.execute(text("UPDATE gov.approvals SET status = 'expired', decided_by = :by, reason = :r, "
                                  "decided_at = now() WHERE id = :id"), {"by": actor, "r": exc.detail, "id": approval_id})
            raise
    status = "approved" if approve else "rejected"
    if approve and row.kind != "action":
        status = "consumed"  # executed now; nothing left to redeem
    with tx(ws) as conn:
        conn.execute(text("UPDATE gov.approvals SET status = :s, decided_by = :by, reason = :r, decided_at = now() "
                          "WHERE id = :id"), {"s": status, "by": actor, "r": reason, "id": approval_id})
        entry = _operator_entry(conn, ws, actor, row.agent_key or FLEET_AGENT_ID,
                                f"approval_{'approved' if approve else 'rejected'}:{row.kind}")
        updated = conn.execute(text("SELECT p.*, a.agent_key FROM gov.approvals p LEFT JOIN gov.agents a ON a.id = p.agent_id "
                                    "WHERE p.id = :id"), {"id": approval_id}).first()
    return entry, _approval_view(updated)


# ---------------------------------------------------------------- agent credentials


def issue_token(client_id: str, client_secret: str) -> dict:
    slug, _, agent_key = client_id.partition(":")
    with tx(PLATFORM) as conn:  # pre-authentication lookup by (workspace slug, agent); returns one row at most
        row = conn.execute(
            text("SELECT a.agent_key, a.status, a.client_secret_hash, w.id AS ws FROM gov.agents a "
                 "JOIN iam.workspaces w ON w.id = a.workspace_id WHERE w.slug = :s AND a.agent_key = :k"),
            {"s": slug, "k": agent_key},
        ).first()
    # Same error for unknown client / bad secret / revoked, so the endpoint can't enumerate agents.
    if row is None or row.status == "revoked" or not keys.check_client_secret(client_secret, row.client_secret_hash):
        raise GovernanceError(401, "invalid_client")
    token, ttl = keys.issue_agent_token(str(row.ws), row.agent_key, ["actions:execute"], get_settings().veyrqun_env)
    return {"access_token": token, "token_type": "Bearer", "expires_in": ttl}


def rotate_secret(agent_key: str, actor: str = "operator", workspace_id: UUID | None = None) -> dict:
    ws = workspace_id or default_workspace()
    row = _find_row(ws, agent_key)
    if row.status == "revoked":
        raise GovernanceError(409, "reinstate the agent before issuing credentials")
    secret, secret_hash = keys.new_client_secret()
    with tx(ws) as conn:
        conn.execute(text("UPDATE gov.agents SET client_secret_hash = :h, secret_rotated_at = now() WHERE id = :a"),
                     {"h": secret_hash, "a": row.id})
        _operator_entry(conn, ws, actor, agent_key, "rotate_secret", row.policy_version)
    return {"client_id": f"{_ws_slug(ws)}:{agent_key}", "client_secret": secret,
            "note": "store the secret now; it is not shown again"}


# ---------------------------------------------------------------- bundles


def current_bundle(workspace_slug: str) -> tuple[bytes, str, int, str, str]:
    from .policy import bundle

    with tx() as conn:
        ws = conn.execute(text("SELECT id FROM iam.workspaces WHERE slug = :s"), {"s": workspace_slug}).scalar()
    if ws is None:
        raise GovernanceError(404, "workspace not found")
    revision, data, _ = _snapshot(ws)
    payload, etag = bundle.build(revision, data)
    kid, sig = bundle.sign(payload)
    return payload, etag, revision, kid, sig
