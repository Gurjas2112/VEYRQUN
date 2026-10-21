from fastapi import APIRouter, Query

from .. import audit

router = APIRouter(tags=["audit"])


@router.get("/audit-log")
def get_audit_log(
    agent_id: str | None = None,
    outcome: str | None = None,
    action_type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return audit.query_entries(agent_id, outcome, action_type, limit, offset)


@router.get("/audit-log/verify")
def verify_audit_chain():
    """Walks the full ledger and recomputes every SHA-256 parent-child link."""
    return audit.verify_chain()
