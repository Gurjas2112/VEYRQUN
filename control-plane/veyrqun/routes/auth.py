"""Browser authentication (backend-for-frontend). Tokens from the IdP never reach the browser:
it only holds an opaque, HttpOnly, Secure, SameSite=Lax session cookie plus a CSRF token."""

from __future__ import annotations

import logging
import time
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .. import audit, demo
from ..auth import directory, sessions
from ..auth.deps import Principal, cookie_names, current_principal
from ..auth.oidc import OIDCError, provider
from ..auth.roles import permissions_for
from ..config import get_settings
from ..counters import client as redis_client
from ..db import PLATFORM, tx

log = logging.getLogger("veyrqun.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

NO_WORKSPACE = "00000000-0000-0000-0000-000000000000"
INVITE_COOKIE = "veyrqun_invite"


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: UUID


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turnstile_token: str | None = Field(None, max_length=4096)


def _safe_next(next_path: str | None) -> str:
    # Only same-site relative paths: never an open redirect.
    if not next_path or not next_path.startswith("/") or next_path.startswith("//") or "\\" in next_path:
        return "/"
    return next_path


def _set_session_cookies(response: Response, session_id: str, s: sessions.Session) -> None:
    secure = get_settings().session_cookie_secure
    session_cookie, csrf_cookie = cookie_names()
    response.set_cookie(session_cookie, session_id, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(csrf_cookie, s.csrf, httponly=False, secure=secure, samesite="lax", path="/")


def _clear_cookies(response: Response) -> None:
    for name in cookie_names():
        response.delete_cookie(name, path="/")


def _audit_auth(workspace_id: str, actor: str, event: str) -> None:
    if workspace_id == NO_WORKSPACE:
        return
    with tx(UUID(workspace_id)) as conn:
        audit.append(conn, workspace_id=UUID(workspace_id), actor=actor, agent_id="*fleet*", action_type=f"auth:{event}",
                     amount=0, currency="USD", outcome="executed", rule_id=None, policy_version=0)


def new_session(user_id: UUID, issuer: str, sub: str, workspace_id: str | None, mfa: bool, auth_time: int,
                idp_sid: str | None = None) -> tuple[str, sessions.Session]:
    ms = directory.memberships(user_id)
    chosen = next((m for m in ms if m["workspace_id"] == workspace_id), ms[0] if ms else None)
    s = sessions.Session(user_id=str(user_id), issuer=issuer, sub=sub,
                         workspace_id=chosen["workspace_id"] if chosen else NO_WORKSPACE,
                         roles=chosen["roles"] if chosen else [], mfa=mfa, auth_time=auth_time, idp_sid=idp_sid)
    return sessions.create(s), s


@router.get("/login")
def login(request: Request, next: str = "/", step_up: bool = False):  # noqa: A002
    try:
        url = provider().authorization_url(next_path=_safe_next(next), step_up=step_up,
                                           invite=request.cookies.get(INVITE_COOKIE))
    except httpx.HTTPError as exc:
        raise HTTPException(503, "identity provider unavailable", headers={"Retry-After": "10"}) from exc
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    base = get_settings().app_base_url.rstrip("/")
    if error or not code or not state:
        return RedirectResponse(f"{base}/?auth_error={error or 'missing_code'}", status_code=302)
    try:
        identity, ctx = provider().complete(code, state)
    except OIDCError:
        return RedirectResponse(f"{base}/?auth_error=invalid_login", status_code=302)
    log.info("oidc login sub=%s amr=%s acr=%s mfa=%s step_up=%s", identity.sub, identity.amr, identity.acr,
             identity.mfa, bool(ctx.get("step_up")))
    if not identity.email or not identity.email_verified:
        return RedirectResponse(f"{base}/?auth_error=email_not_verified", status_code=302)
    try:
        user_id = directory.upsert_user(identity.issuer, identity.sub, identity.email, identity.name)
        workspace = None
        if ctx.get("invite"):
            workspace = str(directory.accept_invite(ctx["invite"], user_id, identity.email))
    except directory.DirectoryError as exc:
        return RedirectResponse(f"{base}/?auth_error={exc.detail.replace(' ', '_')}", status_code=302)

    old_cookie, _ = cookie_names()
    old = sessions.load(request.cookies.get(old_cookie, ""))
    if old and old.user_id == str(user_id):
        workspace = workspace or old.workspace_id  # step-up keeps the active workspace
        sessions.destroy(request.cookies.get(old_cookie, ""))
    session_id, s = new_session(user_id, identity.issuer, identity.sub, workspace, identity.mfa, identity.auth_time,
                                identity.sid)
    _audit_auth(s.workspace_id, f"user:{user_id}", "step_up" if ctx.get("step_up") else
                ("login_mfa" if identity.mfa else "login"))
    response = RedirectResponse(f"{base}{_safe_next(ctx.get('next'))}", status_code=302)
    _set_session_cookies(response, session_id, s)
    response.delete_cookie(INVITE_COOKIE, path="/")
    return response


@router.post("/logout")
def logout(p: Principal = Depends(current_principal)):
    if p.kind != "user" or not p.session_id:
        raise HTTPException(400, "not a browser session")
    sessions.destroy(p.session_id)
    _audit_auth(str(p.workspace_id), p.actor, "logout")
    response = JSONResponse({"logout_url": provider().end_session_url(get_settings().app_base_url)})
    _clear_cookies(response)
    return response


@router.post("/backchannel-logout")
def backchannel_logout(logout_token: str = Form(...)):
    """OIDC Back-Channel Logout: the IdP tells us a session ended (or a user was disabled)."""
    try:
        claims = provider().verify_logout_token(logout_token)
    except OIDCError as exc:
        raise HTTPException(400, str(exc)) from exc
    revoked = 0
    if claims.get("sid"):
        revoked += sessions.revoke_idp_session(claims["sid"])
    if claims.get("sub"):
        with tx(PLATFORM) as conn:
            uid = conn.execute(text("SELECT id FROM iam.users WHERE idp_issuer = :i AND idp_sub = :s"),
                               {"i": claims["iss"], "s": claims["sub"]}).scalar()
        if uid and not claims.get("sid"):
            revoked += sessions.revoke_user(str(uid))
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@router.get("/me")
def me(p: Principal = Depends(current_principal)):
    if p.kind != "user":
        return {"kind": "agent", "agent_id": p.id, "workspace_id": str(p.workspace_id)}
    ident = directory.identity_of(UUID(p.id))
    return {
        "kind": "user", "user_id": p.id, "email": ident["email"], "name": ident["name"],
        "workspace_id": str(p.workspace_id) if str(p.workspace_id) != NO_WORKSPACE else None,
        "roles": sorted(p.roles), "permissions": sorted(p.permissions), "mfa": p.mfa,
        "step_up_fresh": time.time() - p.auth_time <= get_settings().step_up_max_age_s,
        "memberships": directory.memberships(UUID(p.id)),
        "demo": {**demo.status(), "guest": bool(p.session and p.session.demo_guest)},
    }


@router.get("/demo")
def demo_status():
    """Public: is the time-limited demo open, and until when (the login page shows this)."""
    return demo.status()


@router.post("/demo", status_code=201)
def demo_login(request: Request):
    """Anonymous guest login for the public demo: a private sandbox with a seeded fleet, no account needed."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().origins:
        raise HTTPException(403, "origin not allowed")  # no cross-site login CSRF
    ip = request.client.host if request.client else "unknown"
    try:
        session_id, s, ws = demo.create_guest(ip)
    except demo.DemoUnavailable as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from exc
    _audit_auth(str(ws), f"user:{s.user_id}", "demo_guest_created")
    response = JSONResponse({"workspace_id": str(ws), "roles": s.roles, **demo.status()}, status_code=201)
    _set_session_cookies(response, session_id, s)
    return response


@router.post("/switch")
def switch(body: Switch, p: Principal = Depends(current_principal)):
    if p.kind != "user" or p.session is None or p.session_id is None:
        raise HTTPException(400, "not a browser session")
    m = next((m for m in directory.memberships(UUID(p.id)) if m["workspace_id"] == str(body.workspace_id)), None)
    if m is None:
        raise HTTPException(403, "not a member of that workspace")
    p.session.workspace_id, p.session.roles = m["workspace_id"], m["roles"]
    sessions.update(p.session_id, p.session)
    return {"workspace_id": m["workspace_id"], "roles": m["roles"], "permissions": sorted(permissions_for(set(m["roles"])))}


@router.get("/invite/{token}")
def invite(token: str):
    """Remember the invite, then send the user through login; it's accepted on callback if the
    IdP-verified email matches the invited address."""
    base = get_settings().app_base_url.rstrip("/")
    if directory.peek_invite(token) is None:
        return RedirectResponse(f"{base}/?auth_error=invite_invalid", status_code=302)
    response = RedirectResponse("/auth/login?next=/", status_code=302)
    response.set_cookie(INVITE_COOKIE, token, httponly=True, secure=get_settings().session_cookie_secure,
                        samesite="lax", path="/", max_age=900)
    return response


def _verify_turnstile(token: str | None, ip: str | None) -> None:
    secret = get_settings().turnstile_secret
    if not secret:
        return  # disabled in local dev; required by deployment config for the public demo
    if not token:
        raise HTTPException(400, "turnstile token required")
    res = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                     data={"secret": secret, "response": token, "remoteip": ip or ""}, timeout=5)
    if not res.json().get("success"):
        raise HTTPException(403, "turnstile verification failed")


@router.post("/sandbox", status_code=201)
def create_sandbox(request: Request, body: SandboxRequest, p: Principal = Depends(current_principal)):
    """Public self-signup creates only an isolated, quota-limited sandbox that expires in 7 days."""
    if p.kind != "user" or p.session is None or p.session_id is None:
        raise HTTPException(400, "not a browser session")
    ip = request.client.host if request.client else "unknown"
    for key, limit in ((f"vq:rl:sandbox:ip:{ip}", 5), (f"vq:rl:sandbox:user:{p.id}", 3)):
        count = redis_client().incr(key)
        if count == 1:
            redis_client().expire(key, 3600)
        if count > limit:
            raise HTTPException(429, "too many sandbox requests", headers={"Retry-After": "3600"})
    _verify_turnstile(body.turnstile_token, ip)
    ident = directory.identity_of(UUID(p.id))
    try:
        ws = directory.create_sandbox(UUID(p.id), ident["email"] or "")
    except directory.DirectoryError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    m = next(m for m in directory.memberships(UUID(p.id)) if m["workspace_id"] == str(ws))
    p.session.workspace_id, p.session.roles = m["workspace_id"], m["roles"]
    sessions.update(p.session_id, p.session)
    _audit_auth(str(ws), p.actor, "sandbox_created")
    return {"workspace_id": str(ws), "slug": m["slug"], "roles": m["roles"], "expires_in_days": get_settings().sandbox_ttl_days}
