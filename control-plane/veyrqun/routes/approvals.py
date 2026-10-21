import time

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import service
from ..auth.deps import Principal, require
from ..config import get_settings
from ..models import ApprovalDecision
from ..ws import ws_manager

router = APIRouter(tags=["approvals"])

STATUS_PATTERN = "^(pending|approved|rejected|expired|consumed)$"
# Approving one of these *is* the sensitive act (publishing policy, releasing the kill switch, granting admin).
STEP_UP_KINDS = {"policy_publish", "kill_release", "role_grant"}


@router.get("/approvals")
def list_approvals(status: str | None = Query("pending", pattern=STATUS_PATTERN),
                   p: Principal = Depends(require("approvals:read"))):
    approvals = service.list_approvals(status, p.workspace_id)
    if p.roles <= {"app_owner", "viewer"}:  # app owners see the requests they raised
        approvals = [a for a in approvals if a["requested_by"] == p.actor]
    return approvals


def _decide(approval_id: str, approve: bool, body: ApprovalDecision | None, p: Principal):
    approval = service.get_approval(approval_id, p.workspace_id)
    if approve and approval["kind"] in STEP_UP_KINDS and time.time() - p.auth_time > get_settings().step_up_max_age_s:
        raise HTTPException(401, "step-up authentication required",
                            headers={"X-Veyrqun-Step-Up": "/auth/login?step_up=1"})
    return service.decide_approval(approval_id, approve, p.actor, body.reason if body else None, p.workspace_id)


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, body: ApprovalDecision | None = None,
                  p: Principal = Depends(require("approvals:decide"))):
    entry, approval = _decide(approval_id, True, body, p)
    await ws_manager.publish(entry)
    return approval


@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, body: ApprovalDecision | None = None,
                 p: Principal = Depends(require("approvals:decide"))):
    entry, approval = _decide(approval_id, False, body, p)
    await ws_manager.publish(entry)
    return approval
