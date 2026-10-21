"""Service-to-service endpoints for gateways (bearer service token, never browser sessions).

A gateway that gets `require_approval` from the policy creates the pending approval here, lets the agent
poll its status, and consumes it once the approved action has gone through (one-shot).
"""

from __future__ import annotations

import hmac
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .. import service
from ..config import get_settings
from ..db import tx, utcnow
from ..ids import uuid7

router = APIRouter(prefix="/internal", tags=["internal (gateway)"])


def require_service(authorization: str | None = Header(None)) -> None:
    expected = get_settings().bundle_service_token
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(presented, expected):
        raise HTTPException(401, "invalid service token")


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: UUID
    agent_id: str = Field(..., max_length=64)
    action_type: str = Field(..., max_length=64)
    amount: Decimal = Field(..., ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(..., pattern=r"^[A-Z]{3}$")
    decision_id: str = Field(..., max_length=64)


class Consume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: UUID
    decision_id: str = Field(..., max_length=64)


@router.post("/approvals", status_code=201, dependencies=[Depends(require_service)])
def create(body: ApprovalCreate):
    row = service._find_row(body.workspace_id, body.agent_id)
    approval_id = uuid7()
    expires = utcnow() + service.APPROVAL_TTL
    with tx(body.workspace_id) as conn:
        conn.execute(
            text("INSERT INTO gov.approvals (id, workspace_id, agent_id, kind, action_type, amount, currency, requested_by, "
                 "decision_id, expires_at) VALUES (:id, :w, :a, 'action', :act, :amt, :cur, :by, :d, :exp)"),
            {"id": approval_id, "w": body.workspace_id, "a": row.id, "act": body.action_type, "amt": body.amount,
             "cur": body.currency, "by": f"agent:{body.agent_id}", "d": body.decision_id, "exp": expires},
        )
    return {"approval_id": str(approval_id), "status": "pending", "expires_at": expires.isoformat()}


@router.get("/approvals/{approval_id}", dependencies=[Depends(require_service)])
def status(approval_id: UUID, workspace_id: UUID):
    with tx(workspace_id) as conn:
        row = conn.execute(text("SELECT p.status, p.action_type, p.amount, p.currency, p.expires_at, a.agent_key "
                                "FROM gov.approvals p JOIN gov.agents a ON a.id = p.agent_id "
                                "WHERE p.id = :id AND p.kind = 'action'"), {"id": approval_id}).first()
    if row is None:
        raise HTTPException(404, "approval not found")
    state = "expired" if row.status == "pending" and row.expires_at < utcnow() else row.status
    return {"approval_id": str(approval_id), "status": state, "agent_id": row.agent_key, "action_type": row.action_type,
            "amount": str(row.amount), "currency": row.currency.strip()}


@router.post("/approvals/{approval_id}/consume", dependencies=[Depends(require_service)])
def consume(approval_id: UUID, body: Consume):
    with tx(body.workspace_id) as conn:
        updated = conn.execute(
            text("UPDATE gov.approvals SET status = 'consumed', decision_id = :d WHERE id = :id AND status = 'approved' "
                 "AND expires_at > now() RETURNING id"), {"id": approval_id, "d": body.decision_id}).first()
    if updated is None:
        raise HTTPException(409, "approval is not in an approved, unexpired state")
    return {"approval_id": str(approval_id), "status": "consumed"}
