from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from .. import service
from ..metrics import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics")
def get_metrics():
    return {**metrics.snapshot(), "fleet": service.fleet_status()}


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
def prometheus_metrics():
    return metrics.prometheus(service.fleet_status())


@router.get("/health")
def health():
    return {"status": "ok"}
