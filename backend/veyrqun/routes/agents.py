from fastapi import APIRouter

from .. import service
from ..models import AgentCreate, AgentPolicyUpdate
from ..ws import ws_manager

router = APIRouter(tags=["agents & policies"])


@router.get("/agents")
def list_agents():
    return service.list_agents()


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    return service.get_agent(agent_id)


@router.post("/agents", status_code=201)
async def create_agent(agent: AgentCreate):
    entry, created = service.create_agent(agent.model_dump())
    await ws_manager.publish(entry, [created])
    return {"message": "Agent created successfully", "agent": created}


@router.patch("/agents/{agent_id}/policy")
async def update_policy(agent_id: str, update: AgentPolicyUpdate):
    changes = {field: getattr(update, field) for field in update.model_fields_set if field != "note"}
    entry, agent = service.update_policy(agent_id, changes, update.note)
    await ws_manager.publish(entry, [agent])
    return {"message": "Policy updated", "agent_id": agent_id, "policy_version": agent["policy_version"], "agent": agent}


@router.get("/agents/{agent_id}/policy-history")
def policy_history(agent_id: str):
    return service.policy_history(agent_id)


@router.post("/agents/{agent_id}/policy/rollback/{version}")
async def rollback_policy(agent_id: str, version: int):
    entry, agent = service.rollback_policy(agent_id, version)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Rolled back to v{version}", "policy_version": agent["policy_version"], "agent": agent}


@router.post("/agents/{agent_id}/reset-spend")
async def reset_spend(agent_id: str):
    entry, agent = service.reset_spend(agent_id)
    await ws_manager.publish(entry, [agent])
    return {"message": "Spend reset to 0.00", "agent_id": agent_id}


@router.post("/reset-all-spend")
async def reset_all_spend():
    entry, agents = service.reset_all_spend()
    await ws_manager.publish(entry, agents)
    return {"message": "All agent spend reset to 0.00"}
