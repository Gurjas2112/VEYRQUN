"""Users, memberships, invites, sandbox workspaces and erasure (crypto-shredding).

Emails are personal data: stored encrypted under a per-user identity key, looked up through a keyed hash.
The audit chain only ever records `user:<uuid>`, so erasing a person (deleting their key) leaves the chain valid
while the identity can no longer be recovered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text

from .. import audit
from ..config import get_settings
from ..crypto import KeyRing
from ..db import PLATFORM, tx, utcnow
from ..ids import uuid7
from ..keys import keyring
from . import sessions
from .roles import ROLES, sod_violation

INVITE_TTL = timedelta(hours=72)


class DirectoryError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _lookup_key() -> bytes:
    raw = get_settings().veyrqun_lookup_key
    if raw:
        return base64.b64decode(raw)
    return hashlib.sha256(b"veyrqun-insecure-dev-lookup-key").digest()


def email_hash(email: str) -> str:
    return hmac.new(_lookup_key(), email.strip().lower().encode(), hashlib.sha256).hexdigest()


def _user_ring(user_id: UUID, conn) -> KeyRing | None:
    row = conn.execute(text("SELECT key_enc FROM iam.identity_keys WHERE user_id = :u"), {"u": user_id}).first()
    if row is None or row.key_enc is None:
        return None
    dek = keyring().decrypt(bytes(row.key_enc), aad=str(user_id).encode())
    return KeyRing({"u": dek}, "u")


def _audit(conn, ws: UUID, actor: str, event: str, subject: str = "*fleet*") -> None:
    audit.append(conn, workspace_id=ws, actor=actor, agent_id=subject, action_type=f"auth:{event}",
                 amount=0, currency="USD", outcome="executed", rule_id=None, policy_version=0)


# ---------------------------------------------------------------- users


def upsert_user(issuer: str, sub: str, email: str, name: str | None) -> UUID:
    email = email.strip().lower()
    with tx(PLATFORM) as conn:
        row = conn.execute(text("SELECT id, status FROM iam.users WHERE idp_issuer = :i AND idp_sub = :s"),
                           {"i": issuer, "s": sub}).first()
        if row is not None:
            if row.status != "active":
                raise DirectoryError(403, f"account is {row.status}")
            conn.execute(text("UPDATE iam.users SET last_login_at = now() WHERE id = :u"), {"u": row.id})
            return row.id
        user_id = uuid7()
        dek = os.urandom(32)
        ring = KeyRing({"u": dek}, "u")
        conn.execute(
            text("INSERT INTO iam.users (id, idp_issuer, idp_sub, email_hash, email_enc, display_enc, last_login_at) "
                 "VALUES (:id, :i, :s, :h, :e, :n, now())"),
            {"id": user_id, "i": issuer, "s": sub, "h": email_hash(email),
             "e": ring.encrypt(email.encode(), aad=str(user_id).encode()),
             "n": ring.encrypt((name or "").encode(), aad=str(user_id).encode())},
        )
        conn.execute(text("INSERT INTO iam.identity_keys (user_id, key_enc) VALUES (:u, :k)"),
                     {"u": user_id, "k": keyring().encrypt(dek, aad=str(user_id).encode())})
        return user_id


def identity_of(user_id: UUID) -> dict:
    with tx(PLATFORM) as conn:
        row = conn.execute(text("SELECT * FROM iam.users WHERE id = :u"), {"u": user_id}).first()
        if row is None:
            raise DirectoryError(404, "user not found")
        ring = _user_ring(user_id, conn)
    if ring is None:
        return {"user_id": str(user_id), "email": None, "name": None, "status": row.status}
    aad = str(user_id).encode()
    return {"user_id": str(user_id), "email": ring.decrypt(bytes(row.email_enc), aad).decode(),
            "name": ring.decrypt(bytes(row.display_enc), aad).decode() or None, "status": row.status}


def personal_data(user_id: UUID) -> dict:
    """Everything VEYRQUN holds about one person (data-subject access): identity, memberships, sessions and a count of
    ledger entries they authored. Ledger rows reference them only as user:<uuid>; erasure shreds the identity key."""
    with tx(PLATFORM) as conn:
        row = conn.execute(text("SELECT created_at, last_login_at, status FROM iam.users WHERE id = :u"),
                           {"u": user_id}).first()
        authored = conn.execute(text("SELECT count(*) FROM audit.events WHERE actor = :a"),
                                {"a": f"user:{user_id}"}).scalar_one()
    ident = identity_of(user_id)
    return {
        "generated_at": utcnow().isoformat(),
        "identity": {**ident, "created_at": row.created_at.isoformat() if row else None,
                     "last_login_at": row.last_login_at.isoformat() if row and row.last_login_at else None},
        "memberships": memberships(user_id),
        "sessions": sessions.list_for_user(str(user_id)),
        "audit_entries_authored": authored,
        "storage": {"email_and_name": "AES-256-GCM under a per-person key (crypto-shredded on erasure)",
                    "email_lookup": "keyed HMAC, not reversible", "ledger": "pseudonymous actor id only"},
    }


def memberships(user_id: UUID) -> list[dict]:
    with tx(PLATFORM) as conn:
        rows = conn.execute(
            text("SELECT w.id, w.slug, w.name, w.environment, array_agg(m.role ORDER BY m.role) AS roles "
                 "FROM iam.memberships m JOIN iam.workspaces w ON w.id = m.workspace_id "
                 "WHERE m.user_id = :u AND (w.expires_at IS NULL OR w.expires_at > now()) "
                 "GROUP BY w.id ORDER BY w.created_at"),
            {"u": user_id},
        ).all()
    return [{"workspace_id": str(r.id), "slug": r.slug, "name": r.name, "environment": r.environment,
             "roles": list(r.roles)} for r in rows]


def roles_in(user_id: UUID, workspace_id: UUID) -> set[str]:
    with tx(workspace_id) as conn:
        return {r.role for r in conn.execute(
            text("SELECT role FROM iam.memberships WHERE user_id = :u AND workspace_id = :w"),
            {"u": user_id, "w": workspace_id})}


def grant_role(workspace_id: UUID, user_id: UUID, role: str, actor: str) -> None:
    if role not in ROLES:
        raise DirectoryError(422, f"unknown role {role!r}")
    current = roles_in(user_id, workspace_id)
    conflict = sod_violation(current | {role})
    if conflict:
        raise DirectoryError(409, f"separation of duties: {sorted(conflict)} cannot be held together")
    with tx(workspace_id) as conn:
        conn.execute(text("INSERT INTO iam.memberships (workspace_id, user_id, role, granted_by) "
                          "VALUES (:w, :u, :r, :by) ON CONFLICT DO NOTHING"),
                     {"w": workspace_id, "u": user_id, "r": role, "by": actor})
        _audit(conn, workspace_id, actor, f"role_grant:{role}", f"user:{user_id}")
    sessions.revoke_user(str(user_id))  # role changes take effect immediately


def revoke_role(workspace_id: UUID, user_id: UUID, role: str, actor: str) -> None:
    with tx(workspace_id) as conn:
        deleted = conn.execute(text("DELETE FROM iam.memberships WHERE workspace_id = :w AND user_id = :u AND role = :r"),
                               {"w": workspace_id, "u": user_id, "r": role}).rowcount
        if not deleted:
            raise DirectoryError(404, "membership not found")
        _audit(conn, workspace_id, actor, f"role_revoke:{role}", f"user:{user_id}")
    sessions.revoke_user(str(user_id))


def members(workspace_id: UUID) -> list[dict]:
    with tx(workspace_id) as conn:
        rows = conn.execute(text("SELECT user_id, array_agg(role ORDER BY role) AS roles FROM iam.memberships "
                                 "WHERE workspace_id = :w GROUP BY user_id"), {"w": workspace_id}).all()
    return [{**identity_of(r.user_id), "roles": list(r.roles)} for r in rows]


def set_status(user_id: UUID, status: str, actor: str, workspace_id: UUID) -> None:
    with tx(PLATFORM) as conn:
        conn.execute(text("UPDATE iam.users SET status = :s WHERE id = :u"), {"s": status, "u": user_id})
        _audit(conn, workspace_id, actor, f"user_{status}", f"user:{user_id}")
    sessions.revoke_user(str(user_id))


def erase(user_id: UUID, actor: str, workspace_id: UUID) -> None:
    """Crypto-shred: destroy the identity key, drop memberships and sessions. Audit entries remain valid."""
    with tx(PLATFORM) as conn:
        conn.execute(text("UPDATE iam.identity_keys SET key_enc = NULL, shredded_at = now() WHERE user_id = :u"),
                     {"u": user_id})
        conn.execute(text("UPDATE iam.users SET status = 'erased', email_enc = NULL, display_enc = NULL, "
                          "email_hash = 'erased:' || id::text WHERE id = :u"), {"u": user_id})
        conn.execute(text("DELETE FROM iam.memberships WHERE user_id = :u"), {"u": user_id})
        _audit(conn, workspace_id, actor, "user_erased", f"user:{user_id}")
    sessions.revoke_user(str(user_id))


# ---------------------------------------------------------------- invites


def create_invite(workspace_id: UUID, email: str, role: str, actor: str) -> dict:
    if role not in ROLES:
        raise DirectoryError(422, f"unknown role {role!r}")
    token = "vqi_" + secrets.token_urlsafe(32)
    invite_id = uuid7()
    expires = utcnow() + INVITE_TTL
    with tx(workspace_id) as conn:
        conn.execute(
            text("INSERT INTO iam.invites (id, workspace_id, email_hash, email_enc, role, token_hash, created_by, expires_at) "
                 "VALUES (:id, :w, :h, :e, :r, :t, :by, :exp)"),
            {"id": invite_id, "w": workspace_id, "h": email_hash(email),
             "e": keyring().encrypt(email.strip().lower().encode(), aad=str(invite_id).encode()), "r": role,
             "t": hashlib.sha256(token.encode()).hexdigest(), "by": actor, "exp": expires},
        )
        _audit(conn, workspace_id, actor, f"invite:{role}")
    return {"invite_id": str(invite_id), "token": token, "expires_at": expires.isoformat(), "role": role}


def peek_invite(token: str) -> dict | None:
    with tx(PLATFORM) as conn:
        row = conn.execute(text("SELECT * FROM iam.invites WHERE token_hash = :t"),
                           {"t": hashlib.sha256(token.encode()).hexdigest()}).first()
    if row is None or row.accepted_at or row.revoked_at or row.expires_at < utcnow():
        return None
    return {"invite_id": str(row.id), "workspace_id": str(row.workspace_id), "role": row.role}


def accept_invite(token: str, user_id: UUID, email: str) -> UUID:
    """Bound to the invited email: the IdP-verified address must match."""
    with tx(PLATFORM) as conn:
        row = conn.execute(text("SELECT * FROM iam.invites WHERE token_hash = :t FOR UPDATE"),
                           {"t": hashlib.sha256(token.encode()).hexdigest()}).first()
        if row is None or row.accepted_at or row.revoked_at or row.expires_at < utcnow():
            raise DirectoryError(410, "invite is invalid, used or expired")
        if not hmac.compare_digest(row.email_hash, email_hash(email)):
            raise DirectoryError(403, "invite was issued to a different email address")
        conn.execute(text("UPDATE iam.invites SET accepted_at = now(), accepted_by = :u WHERE id = :id"),
                     {"u": user_id, "id": row.id})
    grant_role(row.workspace_id, user_id, row.role, f"invite:{row.id}")
    return row.workspace_id


def revoke_invite(workspace_id: UUID, invite_id: UUID, actor: str) -> None:
    with tx(workspace_id) as conn:
        if not conn.execute(text("UPDATE iam.invites SET revoked_at = now() WHERE id = :id AND accepted_at IS NULL"),
                            {"id": invite_id}).rowcount:
            raise DirectoryError(404, "invite not found or already used")
        _audit(conn, workspace_id, actor, "invite_revoked")


# ---------------------------------------------------------------- sandbox & bootstrap


def create_sandbox(user_id: UUID, email: str, roles: tuple[str, ...] = ("security_operator", "app_owner"),
                   expires_at=None, actor: str = "system:sandbox-signup") -> UUID:
    """Self-signup only ever creates an isolated sandbox with quotas and a 7-day lifetime (or less)."""
    s = get_settings()
    default_exp = utcnow() + timedelta(days=s.sandbox_ttl_days)
    exp = min(default_exp, expires_at) if expires_at else default_exp
    with tx(PLATFORM) as conn:
        existing = conn.execute(text("SELECT count(*) FROM iam.workspaces WHERE owner_user_id = :u AND is_sandbox "
                                     "AND (expires_at IS NULL OR expires_at > now())"), {"u": user_id}).scalar_one()
        if existing:
            raise DirectoryError(409, "you already have an active sandbox")
        ws = uuid7()
        conn.execute(
            text("INSERT INTO iam.workspaces (id, slug, name, environment, is_sandbox, expires_at, owner_user_id, "
                 "quota_agents, quota_requests_per_day) VALUES (:id, :slug, 'Sandbox', 'sandbox', true, :exp, :u, :qa, :qr)"),
            {"id": ws, "slug": f"sbx-{secrets.token_hex(5)}", "exp": exp,
             "u": user_id, "qa": s.sandbox_quota_agents, "qr": s.sandbox_quota_requests_per_day},
        )
        conn.execute(text("INSERT INTO gov.fleet_state (workspace_id) VALUES (:w)"), {"w": ws})
    for role in roles:
        grant_role(ws, user_id, role, actor)
    return ws


def is_live_sandbox(workspace_id: UUID) -> bool:
    with tx(PLATFORM) as conn:
        return bool(conn.execute(text("SELECT 1 FROM iam.workspaces WHERE id = :w AND is_sandbox "
                                      "AND (expires_at IS NULL OR expires_at > now())"), {"w": workspace_id}).first())


def bootstrap_admin(issuer: str, sub: str, email: str, workspace_slug: str = "default") -> UUID:
    """First platform_admin of a fresh install (CLI only, never a web form). Refuses if one exists."""
    with tx(PLATFORM) as conn:
        ws = conn.execute(text("SELECT id FROM iam.workspaces WHERE slug = :s"), {"s": workspace_slug}).scalar()
        if ws is None:
            raise DirectoryError(404, f"workspace {workspace_slug!r} not found")
        if conn.execute(text("SELECT 1 FROM iam.memberships WHERE workspace_id = :w AND role = 'platform_admin'"),
                        {"w": ws}).first():
            raise DirectoryError(409, "a platform_admin already exists; use invites")
    user_id = upsert_user(issuer, sub, email, None)
    grant_role(ws, user_id, "platform_admin", "system:bootstrap")
    return user_id


def purge_expired_sandboxes() -> int:
    """Retention job: sandbox workspaces (and everything in them) are deleted after their TTL."""
    with tx(PLATFORM) as conn:
        expired = [r.id for r in conn.execute(text(
            "SELECT id FROM iam.workspaces WHERE is_sandbox AND expires_at < now()"))]
        for ws in expired:
            conn.execute(text("DELETE FROM gov.approvals WHERE workspace_id = :w"), {"w": ws})
            conn.execute(text("DELETE FROM gov.agents WHERE workspace_id = :w"), {"w": ws})
            conn.execute(text("DELETE FROM gov.fleet_state WHERE workspace_id = :w"), {"w": ws})
            conn.execute(text("DELETE FROM iam.workspaces WHERE id = :w"), {"w": ws})
    return len(expired)
