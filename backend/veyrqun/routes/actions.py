from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from .. import service, simulator
from ..models import ActionAttempt
from ..ws import ws_manager

router = APIRouter(tags=["agent actions"])


@router.post("/simulate/action")
async def simulate_action(attempt: ActionAttempt):
    """Policy enforcement point: every agent action must pass through here before execution."""
    entry, agent = service.attempt_action(attempt.agent_id, attempt.action_type, attempt.amount)
    await ws_manager.publish(entry, [agent])
    return entry


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
