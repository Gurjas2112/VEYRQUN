"""Server-side sessions in Redis (the browser only ever holds an opaque id).

Idle and absolute timeouts depend on role (admins get shorter ones). All sessions of a user are indexed,
so a role change, disablement or erasure revokes them immediately.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass, field

import redis

from ..counters import CounterUnavailable, client

ADMIN_ROLES = frozenset({"platform_admin", "security_operator"})
ADMIN_IDLE_S, ADMIN_ABSOLUTE_S = 15 * 60, 8 * 3600
DEFAULT_IDLE_S, DEFAULT_ABSOLUTE_S = 30 * 60, 12 * 3600


@dataclass
class Session:
    user_id: str
    issuer: str
    sub: str
    workspace_id: str
    roles: list[str]
    mfa: bool
    auth_time: int
    idp_sid: str | None = None
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_seen: int = field(default_factory=lambda: int(time.time()))

    def limits(self) -> tuple[int, int]:
        if ADMIN_ROLES & set(self.roles):
            return ADMIN_IDLE_S, ADMIN_ABSOLUTE_S
        return DEFAULT_IDLE_S, DEFAULT_ABSOLUTE_S


def _key(session_id: str) -> str:
    # Store under a hash of the id, so a Redis dump doesn't leak usable session ids.
    return "vq:sess:" + hashlib.sha256(session_id.encode()).hexdigest()


def _ttl(s: Session, now: int) -> int:
    idle, absolute = s.limits()
    return max(1, min(idle, s.created_at + absolute - now))


def create(s: Session) -> str:
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    try:
        c = client()
        pipe = c.pipeline()
        pipe.set(_key(session_id), json.dumps(asdict(s)), ex=_ttl(s, now))
        pipe.sadd(f"vq:usess:{s.user_id}", _key(session_id))
        if s.idp_sid:
            pipe.sadd(f"vq:idpsid:{s.idp_sid}", _key(session_id))
            pipe.expire(f"vq:idpsid:{s.idp_sid}", DEFAULT_ABSOLUTE_S)
        pipe.execute()
    except redis.RedisError as exc:
        raise CounterUnavailable(str(exc)) from exc
    return session_id


def load(session_id: str) -> Session | None:
    """Returns the session if valid, sliding the idle timeout; None if missing or expired."""
    if not session_id:
        return None
    now = int(time.time())
    try:
        raw = client().get(_key(session_id))
    except redis.RedisError as exc:
        raise CounterUnavailable(str(exc)) from exc
    if raw is None:
        return None
    s = Session(**json.loads(raw))
    idle, absolute = s.limits()
    if now - s.last_seen > idle or now - s.created_at > absolute:
        destroy(session_id)
        return None
    s.last_seen = now
    client().set(_key(session_id), json.dumps(asdict(s)), ex=_ttl(s, now))
    return s


def update(session_id: str, s: Session) -> None:
    client().set(_key(session_id), json.dumps(asdict(s)), ex=_ttl(s, int(time.time())))


def destroy(session_id: str) -> None:
    raw = client().get(_key(session_id))
    client().delete(_key(session_id))
    if raw:
        client().srem(f"vq:usess:{json.loads(raw)['user_id']}", _key(session_id))


def revoke_user(user_id: str) -> int:
    c = client()
    keys = list(c.smembers(f"vq:usess:{user_id}"))
    if keys:
        c.delete(*keys)
    c.delete(f"vq:usess:{user_id}")
    return len(keys)


def revoke_idp_session(idp_sid: str) -> int:
    c = client()
    keys = list(c.smembers(f"vq:idpsid:{idp_sid}"))
    if keys:
        c.delete(*keys)
    c.delete(f"vq:idpsid:{idp_sid}")
    return len(keys)


def list_for_user(user_id: str) -> list[dict]:
    c = client()
    out = []
    for key in c.smembers(f"vq:usess:{user_id}"):
        raw = c.get(key)
        if raw:
            d = json.loads(raw)
            out.append({"session_ref": key.removeprefix("vq:sess:")[:16], "created_at": d["created_at"],
                        "last_seen": d["last_seen"], "workspace_id": d["workspace_id"], "mfa": d["mfa"]})
        else:
            c.srem(f"vq:usess:{user_id}", key)
    return out


def revoke_ref(user_id: str, session_ref: str) -> bool:
    c = client()
    for key in c.smembers(f"vq:usess:{user_id}"):
        if key.removeprefix("vq:sess:").startswith(session_ref):
            c.delete(key)
            c.srem(f"vq:usess:{user_id}", key)
            return True
    return False
