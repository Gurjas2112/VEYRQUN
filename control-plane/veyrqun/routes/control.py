from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .. import service
from ..auth.deps import Principal, require
from ..models import StatusChange
from ..ws import ws_manager

router = APIRouter(tags=["revocation & emergency stop"])


@router.get("/fleet/status")
def fleet_status(p: Principal = Depends(require("agents:read"))):
    return service.fleet_status(p.workspace_id)


@router.post("/emergency-stop")
async def fleet_emergency_stop(p: Principal = Depends(require("fleet:stop"))):
    """Activation is single-person (speed matters); release is two-person in production."""
    entry, agents = service.fleet_stop(actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, agents)
    return {"message": "Emergency stop triggered for all agents", "fleet_stopped": True}


@router.post("/emergency-stop/{agent_id}")
async def agent_emergency_stop(agent_id: str, body: StatusChange | None = None,
                               p: Principal = Depends(require("agents:control"))):
    entry, agent = service.stop_agent(agent_id, body.reason if body else None, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} stopped"}


@router.post("/resume-all")
async def resume_fleet(p: Principal = Depends(require("fleet:release"))):
    if service.workspace_env(p.workspace_id) == "production":
        approval = service.request_change("kill_release", {}, actor=p.actor, workspace_id=p.workspace_id)
        return JSONResponse({"message": "kill-switch release requested; a second person must approve it",
                             "approval": approval}, status_code=202)
    entry, agents = service.fleet_resume(actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, agents)
    return {"message": "All agents resumed (revoked agents stay revoked)", "fleet_stopped": False}


@router.post("/resume/{agent_id}")
async def resume_agent(agent_id: str, p: Principal = Depends(require("agents:control"))):
    entry, agent = service.resume_agent(agent_id, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} resumed"}


@router.post("/agents/{agent_id}/revoke")
async def revoke_agent(agent_id: str, body: StatusChange | None = None, p: Principal = Depends(require("agents:control"))):
    entry, agent = service.revoke_agent(agent_id, body.reason if body else None, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} revoked", "agent": agent}


@router.post("/agents/{agent_id}/reinstate")
async def reinstate_agent(agent_id: str, p: Principal = Depends(require("agents:control"))):
    entry, agent = service.reinstate_agent(agent_id, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} reinstated", "agent": agent}
