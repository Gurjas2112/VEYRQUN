import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .. import keys, service
from ..config import get_settings
from ..models import TokenRequest

router = APIRouter(tags=["agent identity & bundles"])

SLUG = r"^[a-z0-9][a-z0-9-]{1,62}$"


@router.get("/.well-known/jwks.json")
def jwks():
    return keys.jwks()


@router.post("/oauth/token")
async def token(request: Request):
    """OAuth 2.0 client-credentials grant for agents (JSON or form-encoded)."""
    try:
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            payload = dict(await request.form())
        else:
            payload = await request.json()
        body = TokenRequest.model_validate(payload)
    except (ValidationError, ValueError):
        # RFC 6749 §5.2 error shape; never a 500 for a malformed client request.
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers={"Cache-Control": "no-store"})
    result = service.issue_token(body.client_id, body.client_secret)
    return Response(content=json.dumps(result), media_type="application/json",
                    headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/bundles/current")
def current_bundle(
    workspace: str = Query("default", pattern=SLUG),
    workspace_id: UUID | None = None,
    authorization: str | None = Header(None),
    if_none_match: str | None = Header(None),
):
    """Signed OPA bundle (policy.wasm + data.json). Gateways poll this with If-None-Match."""
    expected = get_settings().bundle_service_token
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if expected and not hmac.compare_digest(presented, expected):
        raise HTTPException(401, "invalid service token")
    if get_settings().demo_expired() and service.workspace_is_sandbox(workspace, workspace_id):
        # After the demo window, sandbox bundles are refused: gateways keep the last good one only until
        # BUNDLE_MAX_STALENESS_S, then fail closed for those workspaces.
        raise HTTPException(410, "the public demo has ended")
    payload, etag, revision, kid, signature = service.current_bundle(workspace, workspace_id)
    headers = {
        "ETag": f'"{etag}"',
        "X-Veyrqun-Bundle-Revision": str(revision),
        "X-Veyrqun-Signature": signature,
        "X-Veyrqun-Signature-Kid": kid,
        "Cache-Control": "no-cache",
    }
    if if_none_match and if_none_match.strip('"') == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=payload, media_type="application/gzip", headers=headers)
