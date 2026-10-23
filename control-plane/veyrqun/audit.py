"""Append-only, SHA-256 hash-chained and HMAC-signed audit ledger on PostgreSQL.

entry_hash = SHA-256(canonical_json(fields incl. seq and prev_hash)); version 2 also hashes `details`.
hmac       = HMAC-SHA256(secret[kid], entry_hash). Without the secret, an attacker who rewrites history and
             recomputes every hash still fails verification.
anchors    = signed (seq, head_hash) checkpoints, so even a fully re-hashed and re-HMACed chain is caught
             against an anchor (or its off-database copy).
Appends take a transaction-scoped advisory lock, so the chain stays linear across processes and replicas, and
are idempotent on decision_id (a redelivered event never forks the chain).
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import Connection, text

from . import keys
from .db import PLATFORM, canonical_ts, tx, utcnow
from .ids import uuid7
from .money import fmt, to_decimal

GENESIS = "GENESIS"
CHAIN_LOCK_KEY = 7_042_119_001  # arbitrary, stable advisory-lock id for the global chain
HASH_VERSION = 2

_BASE_FIELDS = (
    "seq", "id", "ts", "workspace_id", "actor", "agent_id", "action_type", "amount", "currency",
    "outcome", "mode", "rule_id", "policy_version", "decision_id", "prev_hash",
)
HASHED_FIELDS = {1: _BASE_FIELDS, 2: (*_BASE_FIELDS, "details", "hash_version")}


def canonical(entry: dict) -> bytes:
    fields = HASHED_FIELDS[entry.get("hash_version", 1)]
    return json.dumps({k: entry[k] for k in fields}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def compute_entry_hash(entry: dict) -> str:
    return hashlib.sha256(canonical(entry)).hexdigest()


def compute_hmac(entry_hash: str, secret: bytes) -> str:
    return hmac_mod.new(secret, entry_hash.encode(), hashlib.sha256).hexdigest()


def _storable(value):
    """Strings (also nested in dicts/lists, keys included) with NUL replaced by U+FFFD."""
    if isinstance(value, str):
        return value.replace(chr(0), chr(0xFFFD))
    if isinstance(value, dict):
        return {_storable(k): _storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_storable(v) for v in value]
    return value


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
    details: dict | None = None,
    ts: datetime | None = None,
) -> dict:
    """Append one entry inside the caller's transaction. Returns the existing entry if `decision_id` was
    already recorded (idempotent)."""
    # Canonicalise to the currency's precision *before* hashing, so the hash matches what
    # NUMERIC(18,2) gives back on read (Decimal("0") would hash as "0" but read back as "0.00").
    amount = to_decimal(amount, currency)
    # Postgres text can't hold NUL; an unwritable entry would let a crafted input keep a decision out of the
    # ledger (the writer would dead-letter it). Replace it before hashing, so the hash covers exactly what's stored.
    actor, agent_id, action_type = _storable(actor), _storable(agent_id), _storable(action_type)
    rule_id = _storable(rule_id) if rule_id is not None else None
    decision_id = _storable(decision_id) if decision_id is not None else None
    details = _storable(details) if details is not None else None
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK_KEY})
    if decision_id is not None:
        existing = conn.execute(text("SELECT seq FROM audit.decision_ids WHERE decision_id = :d"), {"d": decision_id}).scalar()
        if existing is not None:
            row = conn.execute(text("SELECT * FROM audit.events WHERE seq = :s"), {"s": existing}).first()
            return {**row_to_entry(row), "duplicate": True}
    # Global head, not this tenant's: the lookup must bypass row-level security (migration 0005).
    prev = conn.execute(text("SELECT audit.chain_head()")).scalar()
    seq = conn.execute(text("SELECT nextval('audit.events_seq')")).scalar_one()
    entry_id = uuid7()
    # Gateway events carry the decision time, so spend lands on the same local day the gateway reserved it.
    ts = ts or utcnow()
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
        # Round-tripped through JSON so the hashed form equals what jsonb gives back on read.
        "details": json.loads(json.dumps(details or {}, sort_keys=True, default=str)),
        "hash_version": HASH_VERSION,
    }
    entry["entry_hash"] = compute_entry_hash(entry)
    kid, secret = keys.active_hmac()
    entry["hmac"], entry["hmac_kid"] = compute_hmac(entry["entry_hash"], secret), kid
    conn.execute(
        text(
            """
            INSERT INTO audit.events (seq, id, ts, workspace_id, actor, agent_id, action_type, amount, currency,
                                      outcome, mode, rule_id, policy_version, decision_id, prev_hash, entry_hash,
                                      details, hash_version, hmac, hmac_kid)
            VALUES (:seq, :id, :ts_dt, :workspace_id, :actor, :agent_id, :action_type, :amount_dec, :currency,
                    :outcome, :mode, :rule_id, :policy_version, :decision_id, :prev_hash, :entry_hash,
                    CAST(:details_json AS jsonb), :hash_version, :hmac, :hmac_kid)
            """
        ),
        {**entry, "ts_dt": ts, "amount_dec": amount, "details_json": json.dumps(entry["details"])},
    )
    conn.execute(text("INSERT INTO audit.decision_ids (decision_id, seq) VALUES (:d, :s)"),
                 {"d": entry["decision_id"], "s": seq})
    return entry


def row_to_entry(row) -> dict:
    entry = {
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
        "hash_version": row.hash_version,
        "details": row.details or {},
        "hmac": row.hmac,
        "hmac_kid": row.hmac_kid,
    }
    return entry


_QUERY = text(
    "SELECT * FROM audit.events WHERE workspace_id = :ws "
    "AND (CAST(:agent AS text) IS NULL OR agent_id = :agent) "
    "AND (CAST(:outcome AS text) IS NULL OR outcome = :outcome) "
    "AND (CAST(:action AS text) IS NULL OR action_type = :action) "
    "AND (CAST(:before AS bigint) IS NULL OR seq < :before) "
    "ORDER BY seq DESC LIMIT :limit"
)


def query(
    workspace_id: UUID,
    *,
    agent_id: str | None = None,
    outcome: str | None = None,
    action_type: str | None = None,
    limit: int = 50,
    before_seq: int | None = None,
) -> list[dict]:
    """Newest first, keyset-paginated on seq (stable under concurrent inserts, unlike OFFSET).
    One static, fully parameterised statement: optional filters are NULL-tolerant, never string-built."""
    params = {"ws": workspace_id, "agent": agent_id or None, "outcome": outcome or None,
              "action": action_type or None, "before": before_seq, "limit": limit}
    with tx(workspace_id) as conn:
        return [row_to_entry(r) for r in conn.execute(_QUERY, params)]


def query_ascending(workspace_id: UUID, *, since_seq: int, limit: int) -> list[dict]:
    with tx(workspace_id) as conn:
        rows = conn.execute(text("SELECT * FROM audit.events WHERE workspace_id = :ws AND seq > :s ORDER BY seq LIMIT :n"),
                            {"ws": workspace_id, "s": since_seq, "n": limit})
        return [row_to_entry(r) for r in rows]


def get(entry_id: str, scope: UUID | str) -> dict | None:
    with tx(scope) as conn:
        row = conn.execute(text("SELECT * FROM audit.events WHERE id = :id"), {"id": entry_id}).first()
    return row_to_entry(row) if row else None


def by_decision(decision_id: str, scope: UUID | str) -> dict | None:
    with tx(scope) as conn:
        row = conn.execute(text("SELECT * FROM audit.events WHERE decision_id = :d"), {"d": decision_id}).first()
    return row_to_entry(row) if row else None


def verify_entry(entry: dict) -> dict:
    """Check one entry in isolation: its own hash and its HMAC (its link needs the full chain)."""
    hash_ok = compute_entry_hash(entry) == entry["entry_hash"]
    if entry.get("hmac") is None:
        return {"hash_valid": hash_ok, "hmac_valid": None, "valid": hash_ok, "note": "legacy entry without HMAC"}
    secret = keys.hmac_secret(entry["hmac_kid"])
    hmac_ok = secret is not None and hmac_mod.compare_digest(compute_hmac(entry["entry_hash"], secret), entry["hmac"])
    return {"hash_valid": hash_ok, "hmac_valid": hmac_ok, "valid": hash_ok and hmac_ok, "hmac_kid": entry["hmac_kid"]}


# ---------------------------------------------------------------- anchors


def anchor_payload(seq: int, head_hash: str, entries: int) -> bytes:
    return json.dumps({"seq": seq, "head_hash": head_hash, "entries": entries}, sort_keys=True,
                      separators=(",", ":")).encode()


def create_anchor(external_put=None) -> dict | None:
    """Sign and record the current head. `external_put(name, bytes) -> ref` copies it off-database."""
    with tx(PLATFORM) as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK_KEY})
        head = conn.execute(text("SELECT seq, entry_hash FROM audit.events ORDER BY seq DESC LIMIT 1")).first()
        if head is None:
            return None
        last = conn.execute(text("SELECT seq FROM audit.anchors ORDER BY id DESC LIMIT 1")).scalar()
        if last == head.seq:
            return None  # nothing new since the last anchor
        entries = conn.execute(text("SELECT count(*) FROM audit.events")).scalar_one()
        payload = anchor_payload(head.seq, head.entry_hash, entries)
        kid, signature = keys.sign_bytes("audit_anchor", payload)
        ref = None
        if external_put is not None:
            doc = json.dumps({"payload": json.loads(payload), "signature": signature, "kid": kid}).encode()
            ref = external_put(f"anchors/{head.seq:012d}.json", doc)
        row = conn.execute(
            text("INSERT INTO audit.anchors (seq, head_hash, entries, signature, kid, external_ref) "
                 "VALUES (:s, :h, :n, :sig, :kid, :ref) RETURNING id, created_at"),
            {"s": head.seq, "h": head.entry_hash, "n": entries, "sig": signature, "kid": kid, "ref": ref},
        ).first()
    return {"id": row.id, "seq": head.seq, "head_hash": head.entry_hash, "entries": entries, "kid": kid,
            "external_ref": ref, "created_at": row.created_at.isoformat()}


def list_anchors(limit: int = 20) -> list[dict]:
    with tx(PLATFORM) as conn:
        rows = conn.execute(text("SELECT * FROM audit.anchors ORDER BY id DESC LIMIT :n"), {"n": limit}).all()
    return [{"id": a.id, "seq": a.seq, "head_hash": a.head_hash, "entries": a.entries, "kid": a.kid,
             "signature": a.signature, "external_ref": a.external_ref, "created_at": a.created_at.isoformat(),
             "payload": anchor_payload(a.seq, a.head_hash, a.entries).decode()} for a in rows]


def _anchor_signature_ok(anchor) -> bool:
    jwk = next((k for k in keys.jwks()["keys"] if k.get("kid") == anchor.kid), None)
    if jwk is None:
        with tx(PLATFORM) as conn:
            row = conn.execute(text("SELECT public_jwk FROM iam.signing_keys WHERE kid = :k"), {"k": anchor.kid}).first()
        jwk = row.public_jwk if row else None
    if jwk is None:
        return False
    raw = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
    sig = base64.urlsafe_b64decode(anchor.signature + "=" * (-len(anchor.signature) % 4))
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(sig, anchor_payload(anchor.seq, anchor.head_hash, anchor.entries))
        return True
    except InvalidSignature:
        return False


# ---------------------------------------------------------------- verification


def verify_chain(batch: int = 5000) -> dict:
    """Stream the whole ledger in seq order: recompute every hash, link and HMAC, and check every anchor."""
    expected_prev = GENESIS
    checked = legacy = 0
    last_seq = -1
    # The chain is global, so verification needs platform scope; callers only get valid/broken_at back.
    with tx(PLATFORM) as conn:
        anchors = {a.seq: a for a in conn.execute(text("SELECT * FROM audit.anchors ORDER BY id"))}
        for a in anchors.values():
            if not _anchor_signature_ok(a):
                return {"valid": False, "reason": "anchor signature invalid", "anchor_id": a.id, "entries_checked": 0}
        while True:
            rows = conn.execute(
                text("SELECT * FROM audit.events WHERE seq > :s ORDER BY seq LIMIT :n"), {"s": last_seq, "n": batch}
            ).all()
            if not rows:
                break
            for row in rows:
                entry = row_to_entry(row)
                fail = {"valid": False, "broken_at": entry["id"], "seq": entry["seq"], "entries_checked": checked}
                if entry["prev_hash"] != expected_prev:
                    return {**fail, "reason": "prev_hash mismatch"}
                result = verify_entry(entry)
                if not result["hash_valid"]:
                    return {**fail, "reason": "entry_hash invalid"}
                if result["hmac_valid"] is None:
                    legacy += 1
                elif not result["hmac_valid"]:
                    return {**fail, "reason": "hmac invalid"}
                anchor = anchors.get(entry["seq"])
                if anchor is not None and anchor.head_hash != entry["entry_hash"]:
                    return {**fail, "reason": f"does not match signed anchor {anchor.id}"}
                expected_prev = entry["entry_hash"]
                checked += 1
                last_seq = entry["seq"]
    missing = [a.id for s, a in anchors.items() if s > last_seq]
    if missing:
        return {"valid": False, "reason": "chain is shorter than a signed anchor (entries deleted from the end)",
                "anchor_id": missing[0], "entries_checked": checked}
    return {"valid": True, "entries_checked": checked, "head_hash": expected_prev, "anchors_checked": len(anchors),
            "legacy_entries_without_hmac": legacy}
