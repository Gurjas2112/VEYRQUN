import hashlib
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import audit, keys
from ..auth.deps import Principal, require

router = APIRouter(tags=["audit"])

MAX_EXPORT_ROWS = 100_000


@router.get("/audit-log")
def get_audit_log(
    agent_id: str | None = None,
    outcome: str | None = None,
    action_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    before_seq: int | None = Query(None, ge=1),
    p: Principal = Depends(require("audit:read")),
):
    """Newest first, keyset-paginated: pass `before_seq` = the smallest `seq` of the previous page."""
    return audit.query(p.workspace_id, agent_id=agent_id, outcome=outcome, action_type=action_type,
                       limit=limit, before_seq=before_seq)


@router.get("/audit-log/verify")
def verify_audit_chain(_: Principal = Depends(require("audit:verify"))):
    """Walks the full ledger and recomputes every SHA-256 parent-child link."""
    return audit.verify_chain()


@router.get("/audit/export")
def export(
    since_seq: int = Query(0, ge=0),
    limit: int = Query(10_000, ge=1, le=MAX_EXPORT_ROWS),
    p: Principal = Depends(require("audit:export")),
):
    """Signed NDJSON export of this workspace's ledger entries (oldest first) plus a manifest.
    Verify offline with scripts/verify_export.py against the JWKS."""
    rows = audit.query_ascending(p.workspace_id, since_seq=since_seq, limit=limit)
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows).encode()
    manifest = {
        "format": "veyrqun-audit-export/v1",
        "workspace_id": str(p.workspace_id),
        "exported_by": p.actor,
        "exported_at": datetime.now(UTC).isoformat(),
        "count": len(rows),
        "first_seq": rows[0]["seq"] if rows else None,
        "last_seq": rows[-1]["seq"] if rows else None,
        "first_hash": rows[0]["entry_hash"] if rows else None,
        "last_hash": rows[-1]["entry_hash"] if rows else None,
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    kid, signature = keys.sign_bytes("bundle", canonical)
    manifest_line = json.dumps({"manifest": manifest, "signature": signature, "kid": kid}, sort_keys=True)
    return Response(content=body + manifest_line.encode() + b"\n", media_type="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=veyrqun-audit.ndjson",
                             "Cache-Control": "no-store"})


@router.get("/audit/{entry_id}")
def get_entry(entry_id: str, p: Principal = Depends(require("audit:read"))):
    entry = audit.get(entry_id, p.workspace_id)
    if entry is None:
        raise HTTPException(404, "entry not found")
    return entry


@router.get("/audit/{entry_id}/verify")
def verify_entry(entry_id: str, p: Principal = Depends(require("audit:read"))):
    """Recompute one entry's hash in isolation (its link is checked by the full-chain verifier)."""
    entry = audit.get(entry_id, p.workspace_id)
    if entry is None:
        raise HTTPException(404, "entry not found")
    return {"id": entry_id, "valid": audit.compute_entry_hash(entry) == entry["entry_hash"]}
