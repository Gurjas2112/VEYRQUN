"""Append-only, SHA-256 hash-chained audit ledger on PostgreSQL.

entry_hash = SHA-256(canonical_json(fields incl. seq and prev_hash)). Appends take a
transaction-scoped advisory lock, so the chain stays linear across processes and replicas.
Editing, deleting or reordering any row breaks verification at that exact entry.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, text

from .db import canonical_ts, tx, utcnow
from .ids import uuid7
from .money import fmt, to_decimal

GENESIS = "GENESIS"
CHAIN_LOCK_KEY = 7_042_119_001  # arbitrary, stable advisory-lock id for the global chain

HASHED_FIELDS = (
    "seq", "id", "ts", "workspace_id", "actor", "agent_id", "action_type", "amount", "currency",
    "outcome", "mode", "rule_id", "policy_version", "decision_id", "prev_hash",
)


def canonical(entry: dict) -> bytes:
    return json.dumps({k: entry[k] for k in HASHED_FIELDS}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def compute_entry_hash(entry: dict) -> str:
    return hashlib.sha256(canonical(entry)).hexdigest()


def append(
    conn: Connection,
    *,
    workspace_id: UUID,
    actor: str,
    agent_id: str,
    action_type: str,
    amount: Decimal,
    currency: str,
    outcome: str,
    rule_id: str | None,
    policy_version: int,
    decision_id: str | None = None,
    mode: str = "enforce",
) -> dict:
    """Append one entry inside the caller's transaction."""
    # Canonicalise to the currency's precision *before* hashing, so the hash matches what
    # NUMERIC(18,2) gives back on read (Decimal("0") would hash as "0" but read back as "0.00").
    amount = to_decimal(amount, currency)
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK_KEY})
    prev = conn.execute(text("SELECT entry_hash FROM audit.events ORDER BY seq DESC LIMIT 1")).scalar()
    seq = conn.execute(text("SELECT nextval('audit.events_seq')")).scalar_one()
    entry_id = uuid7()
    ts = utcnow()
    entry = {
        "seq": seq,
        "id": str(entry_id),
        "ts": canonical_ts(ts),
        "workspace_id": str(workspace_id),
        "actor": actor,
        "agent_id": agent_id,
        "action_type": action_type,
        "amount": fmt(amount),
        "currency": currency,
        "outcome": outcome,
        "mode": mode,
        "rule_id": rule_id,
        "policy_version": policy_version,
        "decision_id": decision_id or str(entry_id),
        "prev_hash": prev or GENESIS,
    }
    entry["entry_hash"] = compute_entry_hash(entry)
    conn.execute(
        text(
            """
            INSERT INTO audit.events (seq, id, ts, workspace_id, actor, agent_id, action_type, amount, currency,
                                      outcome, mode, rule_id, policy_version, decision_id, prev_hash, entry_hash)
            VALUES (:seq, :id, :ts_dt, :workspace_id, :actor, :agent_id, :action_type, :amount_dec, :currency,
                    :outcome, :mode, :rule_id, :policy_version, :decision_id, :prev_hash, :entry_hash)
            """
        ),
        {**entry, "ts_dt": ts, "amount_dec": amount},
    )
    return entry


def row_to_entry(row) -> dict:
    return {
        "seq": row.seq,
        "id": str(row.id),
        "ts": canonical_ts(row.ts),
        "workspace_id": str(row.workspace_id),
        "actor": row.actor,
        "agent_id": row.agent_id,
        "action_type": row.action_type,
        "amount": fmt(row.amount),
        "currency": row.currency.strip(),
        "outcome": row.outcome,
        "mode": row.mode,
        "rule_id": row.rule_id,
        "policy_version": row.policy_version,
        "decision_id": row.decision_id,
        "prev_hash": row.prev_hash,
        "entry_hash": row.entry_hash,
    }


def query(
    workspace_id: UUID,
    *,
    agent_id: str | None = None,
    outcome: str | None = None,
    action_type: str | None = None,
    limit: int = 50,
    before_seq: int | None = None,
) -> list[dict]:
    """Newest first, keyset-paginated on seq (stable under concurrent inserts, unlike OFFSET)."""
    clauses = ["workspace_id = :ws"]
    params: dict = {"ws": workspace_id, "limit": limit}
    for col, val in (("agent_id", agent_id), ("outcome", outcome), ("action_type", action_type)):
        if val:
            clauses.append(f"{col} = :{col}")
            params[col] = val
    if before_seq is not None:
        clauses.append("seq < :before_seq")
        params["before_seq"] = before_seq
    sql = f"SELECT * FROM audit.events WHERE {' AND '.join(clauses)} ORDER BY seq DESC LIMIT :limit"  # noqa: S608
    with tx(workspace_id) as conn:
        return [row_to_entry(r) for r in conn.execute(text(sql), params)]


def get(entry_id: str) -> dict | None:
    with tx() as conn:
        row = conn.execute(text("SELECT * FROM audit.events WHERE id = :id"), {"id": entry_id}).first()
    return row_to_entry(row) if row else None


def verify_chain(batch: int = 5000) -> dict:
    """Stream the whole ledger in seq order and recompute every hash and link."""
    expected_prev = GENESIS
    checked = 0
    last_seq = -1
    with tx() as conn:
        while True:
            rows = conn.execute(
                text("SELECT * FROM audit.events WHERE seq > :s ORDER BY seq LIMIT :n"), {"s": last_seq, "n": batch}
            ).all()
            if not rows:
                break
            for row in rows:
                entry = row_to_entry(row)
                if entry["prev_hash"] != expected_prev:
                    return {"valid": False, "broken_at": entry["id"], "seq": entry["seq"],
                            "reason": "prev_hash mismatch", "entries_checked": checked}
                if compute_entry_hash(entry) != entry["entry_hash"]:
                    return {"valid": False, "broken_at": entry["id"], "seq": entry["seq"],
                            "reason": "entry_hash invalid", "entries_checked": checked}
                expected_prev = entry["entry_hash"]
                checked += 1
                last_seq = entry["seq"]
    return {"valid": True, "entries_checked": checked, "head_hash": expected_prev}
