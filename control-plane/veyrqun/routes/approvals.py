from fastapi import APIRouter, Query

from .. import service
from ..models import ApprovalDecision
from ..ws import ws_manager

router = APIRouter(tags=["approvals"])

STATUS_PATTERN = "^(pending|approved|rejected|expired|consumed)$"


@router.get("/approvals")
def list_approvals(status: str | None = Query("pending", pattern=STATUS_PATTERN)):
    return service.list_approvals(status)


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, body: ApprovalDecision | None = None):
    entry, approval = service.decide_approval(approval_id, True, "operator", body.reason if body else None)
    await ws_manager.publish(entry)
    return approval


@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, body: ApprovalDecision | None = None):
    entry, approval = service.decide_approval(approval_id, False, "operator", body.reason if body else None)
    await ws_manager.publish(entry)
    return approval
