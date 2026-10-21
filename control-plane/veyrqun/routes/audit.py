from fastapi import APIRouter, HTTPException, Query

from .. import audit, service

router = APIRouter(tags=["audit"])


@router.get("/audit-log")
def get_audit_log(
    agent_id: str | None = None,
    outcome: str | None = None,
    action_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    before_seq: int | None = Query(None, ge=1),
):
    """Newest first. Page with `before_seq` = the smallest `seq` of the previous page."""
    return audit.query(service.default_workspace(), agent_id=agent_id, outcome=outcome, action_type=action_type,
                       limit=limit, before_seq=before_seq)


@router.get("/audit-log/verify")
def verify_audit_chain():
    """Walks the full ledger and recomputes every SHA-256 parent-child link."""
    return audit.verify_chain()


@router.get("/audit/{entry_id}")
def get_entry(entry_id: str):
    entry = audit.get(entry_id)
    if entry is None:
        raise HTTPException(404, "entry not found")
    return entry
