from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .. import service, simulator
from ..auth import sessions
from ..auth.deps import Principal, cookie_names, current_principal, require
from ..models import ActionAttempt
from ..ws import ws_manager

router = APIRouter(tags=["agent actions"])


@router.post("/simulate/action")
async def simulate_action(attempt: ActionAttempt, p: Principal = Depends(current_principal)):
    """Legacy enforcement path (direct API). Same OPA policy + Redis counters as the gateway.
    Agents call it with their own JWT (and may only act as themselves); operators may simulate."""
    if p.kind == "agent":
        if p.id != attempt.agent_id:
            raise HTTPException(403, "an agent token may only act as its own agent")
    elif "actions:simulate" not in p.permissions:
        raise HTTPException(403, "permission denied: actions:simulate")
    entry, agent, details = service.attempt_action(
        attempt.agent_id, attempt.action_type, attempt.amount, actor=p.actor if p.kind == "user" else None,
        data_classes=attempt.data_classes, approval_id=attempt.approval_id, workspace_id=p.workspace_id,
    )
    await ws_manager.publish(entry, [agent] if agent else None)
    return JSONResponse({**entry, **details}, status_code=202 if details["approval_id"] else 200)


@router.get("/simulator")
def simulator_status(_: Principal = Depends(require("metrics:read"))):
    return {"simulator_running": simulator.state["running"]}


@router.post("/simulator/toggle")
def toggle_simulator(running: bool = Query(...), _: Principal = Depends(require("simulator:toggle"))):
    simulator.state["running"] = running
    return {"simulator_running": running}


@router.websocket("/ws/live-feed")
async def live_feed(websocket: WebSocket):
    """Authenticated by the session cookie (same-site); only the session's workspace is streamed."""
    session_cookie, _ = cookie_names()
    s = sessions.load(websocket.cookies.get(session_cookie, ""))
    if s is None or not s.roles:
        await websocket.close(code=4401)
        return
    await ws_manager.connect(websocket, s.workspace_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
