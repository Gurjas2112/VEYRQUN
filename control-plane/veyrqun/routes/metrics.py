import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .. import service
from ..auth.deps import Principal, require
from ..config import get_settings
from ..metrics import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics")
def get_metrics(p: Principal = Depends(require("metrics:read"))):
    return {**metrics.snapshot(), "fleet": service.fleet_status(p.workspace_id)}


@router.get("/stats")
def workspace_stats(hours: int = Query(24, ge=1, le=24 * 31), p: Principal = Depends(require("metrics:read"))):
    """Decision statistics for the caller's workspace only (from its ledger)."""
    return service.workspace_stats(p.workspace_id, hours)


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def prometheus_metrics(authorization: str | None = Header(None)):
    """Scrape endpoint. Requires `Authorization: Bearer <METRICS_TOKEN>` when a token is configured."""
    expected = get_settings().metrics_token
    if expected and not hmac.compare_digest((authorization or "").removeprefix("Bearer ").strip(), expected):
        raise HTTPException(401, "invalid metrics token")
    return metrics.prometheus(service.fleet_status())


@router.get("/health")
def health():
    return {"status": "ok"}
