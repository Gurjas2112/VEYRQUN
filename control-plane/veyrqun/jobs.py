"""Scheduled integrity and retention jobs. Each writes a signed result into the audit chain, so
"we checked, and here is what we found" is itself tamper-evident.

* chain_check        - full SHA-256 chain verification
* reconcile_spend    - live Redis spend vs the ledger (approved decisions since local midnight or the
                       last reset); drift > 0.01 is an alert. Also snapshots spend into Postgres.
* rebuild_counters   - restore today's counters from the ledger after Redis data loss (fail closed until done)
* purge_sandboxes    - delete sandbox workspaces past their TTL
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from . import audit, counters, keys
from .auth import directory
from .db import PLATFORM, tx, utcnow
from .money import from_minor, to_minor

log = logging.getLogger("veyrqun.jobs")
DRIFT_TOLERANCE = Decimal("0.01")


def _record(workspace_id: UUID, job: str, result: dict) -> dict:
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()
    kid, signature = keys.sign_bytes("bundle", canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    with tx(workspace_id) as conn:
        entry = audit.append(conn, workspace_id=workspace_id, actor="system:jobs", agent_id="*fleet*",
                             action_type=f"integrity:{job}", amount=Decimal("0"), currency="USD",
                             outcome="executed", rule_id=None if result.get("ok") else f"{job}_failed",
                             policy_version=0, decision_id=f"result-sha256:{digest}")
    return {**result, "signature": signature, "kid": kid, "result_sha256": digest, "audit_entry_id": entry["id"]}


def chain_check(workspace_id: UUID) -> dict:
    result = audit.verify_chain()
    out = _record(workspace_id, "chain_check", {"ok": result["valid"], "checked_at": utcnow().isoformat(), **result})
    if not result["valid"]:
        log.error("AUDIT CHAIN BROKEN: %s", result)
    return out


def _ledger_spend_minor(conn, workspace_id: UUID, agent_key: str, tz: str, currency: str) -> int:
    local_midnight = datetime.now(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Order by the chain sequence, not timestamps: two events can share a clock tick (Windows clocks are
    # ~1 ms granular), and a decision right after a reset must still count.
    last_reset_seq = conn.execute(
        text("SELECT max(seq) FROM audit.events WHERE workspace_id = :w AND ts >= :m AND "
             "((agent_id = :a AND action_type = 'operator:reset_spend') OR action_type = 'operator:reset_all_spend')"),
        {"w": workspace_id, "m": local_midnight, "a": agent_key}).scalar() or 0
    total = conn.execute(
        text("SELECT coalesce(sum(amount), 0) FROM audit.events WHERE workspace_id = :w AND agent_id = :a "
             "AND ts >= :m AND seq > :after AND outcome IN ('allow', 'redact') AND mode = 'enforce' "
             "AND action_type NOT LIKE 'operator:%'"),
        {"w": workspace_id, "a": agent_key, "m": local_midnight, "after": last_reset_seq}).scalar_one()
    return to_minor(Decimal(total), currency)


def reconcile_spend(workspace_id: UUID) -> dict:
    drifts, checked = [], 0
    with tx(workspace_id) as conn:
        agents = conn.execute(text("SELECT id, agent_key, timezone, currency FROM gov.agents WHERE workspace_id = :w"),
                              {"w": workspace_id}).all()
        for a in agents:
            ledger = _ledger_spend_minor(conn, workspace_id, a.agent_key, a.timezone, a.currency)
            live = counters.spend_today_minor(str(workspace_id), a.agent_key, a.timezone)
            checked += 1
            if abs(from_minor(live - ledger, a.currency)) > DRIFT_TOLERANCE:
                drifts.append({"agent_id": a.agent_key, "live": str(from_minor(live, a.currency)),
                               "ledger": str(from_minor(ledger, a.currency))})
            conn.execute(
                text("INSERT INTO gov.spend_snapshots (agent_id, local_date, spend, currency) VALUES (:a, :d, :s, :c) "
                     "ON CONFLICT (agent_id, local_date) DO UPDATE SET spend = EXCLUDED.spend, taken_at = now()"),
                {"a": a.id, "d": counters.local_date(a.timezone), "s": from_minor(live, a.currency), "c": a.currency})
    if drifts:
        log.error("SPEND DRIFT between Redis and the ledger: %s", drifts)
    return _record(workspace_id, "reconcile_spend", {"ok": not drifts, "agents_checked": checked, "drifts": drifts})


def rebuild_counters(workspace_id: UUID) -> int:
    """After Redis loses data: recompute today's spend per agent from the ledger and restore it."""
    restored = 0
    with tx(workspace_id) as conn:
        for a in conn.execute(text("SELECT agent_key, timezone, currency FROM gov.agents WHERE workspace_id = :w"),
                              {"w": workspace_id}).all():
            ledger = _ledger_spend_minor(conn, workspace_id, a.agent_key, a.timezone, a.currency)
            key = counters.keys(str(workspace_id), a.agent_key, "_", a.timezone)[0]
            counters.client().set(key, ledger, ex=2 * 86_400)
            restored += 1
    return restored


def all_workspaces() -> list[UUID]:
    with tx(PLATFORM) as conn:
        return [r.id for r in conn.execute(text("SELECT id FROM iam.workspaces WHERE expires_at IS NULL OR expires_at > now()"))]


def purge_sandboxes() -> int:
    return directory.purge_expired_sandboxes()
