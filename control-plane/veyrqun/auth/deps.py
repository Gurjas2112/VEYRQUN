"""Who is calling, and may they do this? FastAPI dependencies used by every route.

* Humans: opaque session cookie -> Redis session -> roles in the active workspace.
  State-changing requests also need the CSRF token (double submit) and an allowed Origin.
* Agents: `Authorization: Bearer <Ed25519 JWT>` issued by /oauth/token, verified against our own keys.
* Privileged roles must have authenticated with MFA; STEP_UP permissions need a recent authentication.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request

from .. import keys
from ..config import get_settings
from . import sessions
from .roles import MFA_REQUIRED, STEP_UP, permissions_for

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def cookie_names() -> tuple[str, str]:
    """`__Host-` requires Secure; plain http dev (non-localhost) may disable it explicitly."""
    if get_settings().session_cookie_secure:
        return "__Host-veyrqun_session", "__Host-veyrqun_csrf"
    return "veyrqun_session", "veyrqun_csrf"


@dataclass
class Principal:
    kind: str                      # "user" | "agent"
    id: str                        # user uuid or agent key
    workspace_id: UUID
    roles: frozenset[str] = frozenset()
    mfa: bool = False
    auth_time: int = 0
    session_id: str | None = None
    session: sessions.Session | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    @property
    def actor(self) -> str:
        return f"{self.kind}:{self.id}"


def _agent_principal(token: str) -> Principal:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "EdDSA":
            raise jwt.InvalidAlgorithmError("only EdDSA agent tokens are accepted")
        jwk = next((k for k in keys.jwks()["keys"] if k["kid"] == header.get("kid")), None)
        if jwk is None:
            raise jwt.InvalidKeyError("unknown kid")
        claims = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["EdDSA"], audience=s.agent_token_audience,
                            issuer=s.jwt_issuer, options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except (jwt.PyJWTError, StopIteration) as exc:
        raise HTTPException(401, "invalid agent token", headers={"WWW-Authenticate": "Bearer"}) from exc
    return Principal(kind="agent", id=claims["sub"], workspace_id=UUID(claims["ws"]),
                     permissions=frozenset({"actions:execute"}))


def current_principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return _agent_principal(auth[7:].strip())
    session_cookie, _ = cookie_names()
    session_id = request.cookies.get(session_cookie)
    s = sessions.load(session_id) if session_id else None
    if s is None:
        raise HTTPException(401, "authentication required")
    if request.method in UNSAFE:
        _check_csrf(request, s)
    roles = frozenset(s.roles)
    return Principal(kind="user", id=s.user_id, workspace_id=UUID(s.workspace_id), roles=roles, mfa=s.mfa,
                     auth_time=s.auth_time, session_id=session_id, session=s, permissions=permissions_for(roles))


def _check_csrf(request: Request, s: sessions.Session) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().origins:
        raise HTTPException(403, "origin not allowed")
    presented = request.headers.get("x-csrf-token", "")
    if not presented or not hmac.compare_digest(presented, s.csrf):
        raise HTTPException(403, "missing or invalid CSRF token")


def require(permission: str):
    """Dependency factory: the caller must hold `permission` in the active workspace."""

    def check(principal: Principal = Depends(current_principal)) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(403, f"permission denied: {permission}")
        if principal.kind == "user":
            if principal.roles & MFA_REQUIRED and not principal.mfa:
                raise HTTPException(403, "multi-factor authentication required for this role")
            if permission in STEP_UP and int(time.time()) - principal.auth_time > get_settings().step_up_max_age_s:
                raise HTTPException(401, "step-up authentication required",
                                    headers={"X-Veyrqun-Step-Up": "/auth/login?step_up=1"})
        return principal

    check.__name__ = f"require_{permission.replace(':', '_')}"
    return check


def principal_optional(request: Request) -> Principal | None:
    try:
        return current_principal(request)
    except HTTPException:
        return None
