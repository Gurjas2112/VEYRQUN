"""Append-only, SHA-256 hash-chained audit ledger.

Each entry hashes its own fields plus the previous entry's hash, so editing or deleting
any historical row breaks every hash after it and `verify_chain` pinpoints where.
"""

import hashlib
import json
import sqlite3
import uuid
from typing import Optional

from .db import get_db, utcnow

GENESIS = "GENESIS"
HASHED_FIELDS = (
    "action_id",
    "agent_id",
    "action_type",
    "amount",
    "timestamp",
    "outcome",
    "rule_triggered",
    "policy_version",
    "prev_hash",
)


def compute_entry_hash(entry_data: dict) -> str:
    payload = json.dumps({k: entry_data[k] for k in HASHED_FIELDS}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_entry(
    conn: sqlite3.Connection,
    agent_id: str,
    action_type: str,
    amount: float,
    outcome: str,
    rule_triggered: Optional[str],
    policy_version: int,
) -> dict:
    """Write one ledger entry inside the caller's transaction (caller holds write_lock)."""
    last = conn.execute("SELECT entry_hash FROM audit_logs ORDER BY rowid DESC LIMIT 1").fetchone()
    entry = {
        "action_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "action_type": action_type,
        "amount": round(amount, 2),
        "timestamp": utcnow(),
        "outcome": outcome,
        "rule_triggered": rule_triggered,
        "policy_version": policy_version,
        "prev_hash": last["entry_hash"] if last else GENESIS,
    }
    entry["entry_hash"] = compute_entry_hash(entry)
    conn.execute(
        """
        INSERT INTO audit_logs (action_id, agent_id, action_type, amount, timestamp, outcome, rule_triggered,
                                policy_version, prev_hash, entry_hash)
        VALUES (:action_id, :agent_id, :action_type, :amount, :timestamp, :outcome, :rule_triggered,
                :policy_version, :prev_hash, :entry_hash)
        """,
        entry,
    )
    return entry


def query_entries(
    agent_id: Optional[str] = None,
    outcome: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    query = "SELECT * FROM audit_logs WHERE 1=1"
    params: list = []
    if agent_id:
        query += " AND agent_id = ?"
        params.append(agent_id)
    if outcome:
        query += " AND outcome = ?"
        params.append(outcome)
    if action_type:
        query += " AND action_type = ?"
        params.append(action_type)
    query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def verify_chain() -> dict:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY rowid ASC").fetchall()
    finally:
        conn.close()

    expected_prev = GENESIS
    for row in rows:
        entry = dict(row)
        if entry["prev_hash"] != expected_prev:
            return {"valid": False, "broken_at": entry["action_id"], "reason": "prev_hash mismatch", "entries_checked": len(rows)}
        if compute_entry_hash(entry) != entry["entry_hash"]:
            return {"valid": False, "broken_at": entry["action_id"], "reason": "entry_hash invalid", "entries_checked": len(rows)}
        expected_prev = entry["entry_hash"]

    return {"valid": True, "entries_checked": len(rows), "head_hash": expected_prev}
