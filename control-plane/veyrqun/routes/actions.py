from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .. import service, simulator
from ..models import ActionAttempt
from ..ws import ws_manager

router = APIRouter(tags=["agent actions"])


@router.post("/simulate/action")
async def simulate_action(attempt: ActionAttempt):
    """Legacy enforcement path (direct API). Same OPA policy + Redis counters as the gateway."""
    entry, agent, details = service.attempt_action(
        attempt.agent_id, attempt.action_type, attempt.amount,
        data_classes=attempt.data_classes, approval_id=attempt.approval_id,
    )
    await ws_manager.publish(entry, [agent] if agent else None)
    return JSONResponse({**entry, **details}, status_code=202 if details["approval_id"] else 200)


@router.get("/simulator")
def simulator_status():
    return {"simulator_running": simulator.state["running"]}


@router.post("/simulator/toggle")
def toggle_simulator(running: bool = Query(...)):
    simulator.state["running"] = running
    return {"simulator_running": running}


@router.websocket("/ws/live-feed")
async def live_feed(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
