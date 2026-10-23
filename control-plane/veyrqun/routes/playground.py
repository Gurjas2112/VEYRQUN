"""Playground: try any input against the real enforcement path.

The control plane mints a short-lived agent token for one of the caller's agents (with the `veyrqun:explain` scope)
and sends the request through the gateway. Classification, the injection signal, the Rego decision, the Redis
counters, redaction and output scanning are the production code path, so the answer is exactly what that agent
would get. Not available in production workspaces.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StringConstraints

from .. import audit, audit_writer, explain, keys, service
from ..auth.deps import Principal, require
from ..config import get_settings
from ..models import ActionName, Money, Strict
from ..policy.store import DEFAULT_SETTINGS

router = APIRouter(prefix="/playground", tags=["playground"])

AgentKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_\-]{2,63}$")]


class PlaygroundRun(Strict):
    agent_id: AgentKey
    kind: Literal["tool", "chat"] = "tool"
    action: ActionName | None = None              # tool name (kind=tool)
    amount: Money = Decimal("0")
    text: str = Field("", max_length=4000)        # free text: tool note, or the chat prompt
    approval_id: str | None = Field(None, max_length=64)


def _tool_args(action: str, amount: Decimal, text: str) -> dict:
    pointer = DEFAULT_SETTINGS["tool_amounts"].get(action)
    args: dict = {}
    if pointer:
        args[pointer.lstrip("/")] = f"{amount:.2f}"
    elif amount:
        args["amount"] = f"{amount:.2f}"  # not a metered tool: the gateway records it as amount 0 (explained below)
    if text:
        args["note"] = text
    return args


@router.post("/run")
def run(body: PlaygroundRun, p: Principal = Depends(require("actions:simulate"))):
    if p.kind != "user":
        raise HTTPException(403, "the playground is for people; agents call the gateway directly")
    env = service.workspace_env(p.workspace_id)
    if env == "production":
        raise HTTPException(403, "the playground is disabled in production workspaces")
    if body.kind == "tool" and not body.action:
        raise HTTPException(422, "action is required for a tool call")
    service.get_agent(body.agent_id, p.workspace_id)  # 404 if it isn't one of this workspace's agents
    spend_before = service.get_agent(body.agent_id, p.workspace_id)["spend_used_today"]
    token, _ = keys.issue_agent_token(str(p.workspace_id), body.agent_id, ["actions:execute", "veyrqun:explain"], env)
    headers = {"authorization": f"Bearer {token}"}
    if body.approval_id:
        headers["x-veyrqun-approval"] = body.approval_id
    gw = get_settings().gateway_url.rstrip("/")
    if body.kind == "tool":
        url, payload = f"{gw}/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": body.action, "arguments": _tool_args(body.action, body.amount, body.text)}}
    else:
        url, payload = f"{gw}/v1/chat/completions", {"model": get_settings().playground_model, "max_tokens": 1024,
                                                      "messages": [{"role": "user", "content": body.text or "Hello"}]}
    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"gateway unreachable: {type(exc).__name__}") from exc
    try:
        response = r.json()
    except ValueError:
        response = {"raw": r.text[:2000]}
    detail = json.loads(r.headers.get("x-veyrqun-explain") or "{}")
    err = response.get("error") if isinstance(response, dict) else None
    if isinstance(err, dict) and isinstance(err.get("data"), dict):
        err = err["data"]  # JSON-RPC error from /mcp
    error_type = err.get("type") if isinstance(err, dict) else None
    outcome = detail.get("outcome")
    if r.status_code == 202:
        outcome = "require_approval"
    elif error_type == "response_policy_violation":
        outcome = "block"
    rule_id = detail.get("rule_id") or (err.get("rule_id") if isinstance(err, dict) else None)
    decision_id = r.headers.get("x-veyrqun-decision-id")
    entry = None
    # The decision event reaches the ledger through the audit writer (milliseconds normally). Wait a moment for it
    # so the visitor sees the signed entry; if the writer is behind, the UI says so instead of blocking.
    for _ in range(15 if decision_id else 0):
        audit_writer.drain_opportunistically()
        entry = audit.by_decision(decision_id, p.workspace_id)
        if entry is not None:
            break
        time.sleep(0.2)
    return {
        "http_status": r.status_code,
        "outcome": outcome or ("allow" if r.status_code == 200 else "refused"),
        "rule_id": rule_id,
        "reason": explain.reason(outcome, rule_id, error_type),
        "decision_id": decision_id,
        "approval_id": response.get("approval_id") if isinstance(response, dict) else None,
        "detail": detail,
        "response": response,
        "spend_before": spend_before,
        "spend_after": service.get_agent(body.agent_id, p.workspace_id)["spend_used_today"],
        "audit_entry": entry,
        "request": payload,
    }
