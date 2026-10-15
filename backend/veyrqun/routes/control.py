from typing import Optional

from fastapi import APIRouter

from .. import service
from ..models import StatusChange
from ..ws import ws_manager

router = APIRouter(tags=["revocation & emergency stop"])


@router.get("/fleet/status")
def fleet_status():
    return service.fleet_status()


@router.post("/emergency-stop")
async def fleet_emergency_stop():
    entry, agents = service.fleet_stop()
    await ws_manager.publish(entry, agents)
    return {"message": "Emergency stop triggered for all agents", "fleet_stopped": True}


@router.post("/emergency-stop/{agent_id}")
async def agent_emergency_stop(agent_id: str, body: Optional[StatusChange] = None):
    entry, agent = service.stop_agent(agent_id, body.reason if body else None)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} stopped"}


@router.post("/resume-all")
async def resume_fleet():
    entry, agents = service.fleet_resume()
    await ws_manager.publish(entry, agents)
    return {"message": "All agents resumed (revoked agents stay revoked)", "fleet_stopped": False}


@router.post("/resume/{agent_id}")
async def resume_agent(agent_id: str):
    entry, agent = service.resume_agent(agent_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} resumed"}


@router.post("/agents/{agent_id}/revoke")
async def revoke_agent(agent_id: str, body: Optional[StatusChange] = None):
    entry, agent = service.revoke_agent(agent_id, body.reason if body else None)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} revoked", "agent": agent}


@router.post("/agents/{agent_id}/reinstate")
async def reinstate_agent(agent_id: str):
    entry, agent = service.reinstate_agent(agent_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Agent {agent_id} reinstated", "agent": agent}
