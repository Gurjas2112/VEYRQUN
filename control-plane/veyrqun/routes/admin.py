"""Workspace administration: members, roles, invites, users, sessions, signing keys.
Every mutation is `iam:manage` (platform_admin, MFA + recent step-up) and lands in the audit chain."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from .. import keys, service
from ..auth import directory, sessions
from ..auth.deps import Principal, require
from ..auth.roles import ROLES
from ..config import get_settings
from ..db import tx

router = APIRouter(prefix="/admin", tags=["administration"])

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[a-z]{2,63}$", re.IGNORECASE)
Role = Literal["platform_admin", "security_operator", "approver", "auditor", "app_owner", "viewer"]


class InviteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    role: Role

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not EMAIL.match(v.strip()):
            raise ValueError("invalid email address")
        return v.strip().lower()


class RoleGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role


class KeyRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["agent_jwt", "bundle", "audit_hmac", "audit_anchor"]


def _member_of_my_workspace(user_id: UUID, p: Principal) -> None:
    if not directory.roles_in(user_id, p.workspace_id):
        raise HTTPException(404, "user not found in this workspace")


def _wrap(fn, *args):
    try:
        return fn(*args)
    except directory.DirectoryError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/members")
def members(p: Principal = Depends(require("iam:read"))):
    return directory.members(p.workspace_id)


@router.post("/invites", status_code=201)
def invite(body: InviteCreate, p: Principal = Depends(require("iam:manage"))):
    result = _wrap(directory.create_invite, p.workspace_id, body.email, body.role, p.actor)
    # No mail server is configured: the platform admin delivers this link out of band.
    result["accept_url"] = f"{get_settings().oidc_redirect_uri.rsplit('/auth/', 1)[0]}/auth/invite/{result['token']}"
    return result


@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(invite_id: UUID, p: Principal = Depends(require("iam:manage"))):
    _wrap(directory.revoke_invite, p.workspace_id, invite_id, p.actor)


@router.post("/members/{user_id}/roles")
def grant(user_id: UUID, body: RoleGrant, p: Principal = Depends(require("iam:manage"))):
    _member_of_my_workspace(user_id, p)
    if body.role == "platform_admin":
        approval = service.request_change("role_grant", {"user_id": str(user_id), "role": body.role},
                                          actor=p.actor, workspace_id=p.workspace_id)
        return JSONResponse({"message": "admin role grants need a second person", "approval": approval}, status_code=202)
    _wrap(directory.grant_role, p.workspace_id, user_id, body.role, p.actor)
    return {"user_id": str(user_id), "roles": sorted(directory.roles_in(user_id, p.workspace_id))}


@router.delete("/members/{user_id}/roles/{role}")
def revoke(user_id: UUID, role: str, p: Principal = Depends(require("iam:manage"))):
    if role not in ROLES:
        raise HTTPException(422, "unknown role")
    if str(user_id) == p.id and role == "platform_admin":
        raise HTTPException(409, "you cannot remove your own admin role")
    _wrap(directory.revoke_role, p.workspace_id, user_id, role, p.actor)
    return {"user_id": str(user_id), "roles": sorted(directory.roles_in(user_id, p.workspace_id))}


@router.post("/users/{user_id}/disable")
def disable(user_id: UUID, p: Principal = Depends(require("iam:manage"))):
    _member_of_my_workspace(user_id, p)
    if str(user_id) == p.id:
        raise HTTPException(409, "you cannot disable yourself")
    directory.set_status(user_id, "disabled", p.actor, p.workspace_id)
    return {"user_id": str(user_id), "status": "disabled"}


@router.post("/users/{user_id}/erase")
def erase(user_id: UUID, p: Principal = Depends(require("iam:manage"))):
    """Right to erasure via crypto-shredding: identity unrecoverable, audit chain intact."""
    _member_of_my_workspace(user_id, p)
    directory.erase(user_id, p.actor, p.workspace_id)
    return {"user_id": str(user_id), "status": "erased"}


@router.get("/users/{user_id}/sessions")
def user_sessions(user_id: UUID, p: Principal = Depends(require("iam:read"))):
    _member_of_my_workspace(user_id, p)
    return sessions.list_for_user(str(user_id))


@router.delete("/users/{user_id}/sessions/{session_ref}", status_code=204)
def revoke_session(user_id: UUID, session_ref: str, p: Principal = Depends(require("iam:manage"))):
    _member_of_my_workspace(user_id, p)
    if not sessions.revoke_ref(str(user_id), session_ref):
        raise HTTPException(404, "session not found")


@router.post("/keys/rotate")
def rotate_key(body: KeyRotate, p: Principal = Depends(require("keys:rotate"))):
    kid = keys.rotate(body.purpose)
    with tx(p.workspace_id) as conn:
        service._operator_entry(conn, p.workspace_id, p.actor, "*fleet*", f"key_rotated:{body.purpose}")
    return {"purpose": body.purpose, "active_kid": kid}
