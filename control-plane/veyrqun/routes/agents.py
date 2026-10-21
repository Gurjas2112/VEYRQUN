from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .. import service
from ..auth.deps import Principal, require
from ..models import AgentCreate, AgentPolicyUpdate
from ..ws import ws_manager

router = APIRouter(tags=["agents & policies"])


def _needs_checker(p: Principal) -> bool:
    """Production publishes, and anything an app owner submits, go through maker-checker."""
    return service.workspace_env(p.workspace_id) == "production" or "security_operator" not in p.roles


def _owned_only(p: Principal) -> bool:
    return p.kind == "user" and p.roles <= {"app_owner", "viewer"} and "app_owner" in p.roles


@router.get("/agents")
def list_agents(p: Principal = Depends(require("agents:read"))):
    agents = service.list_agents(p.workspace_id)
    if _owned_only(p):
        agents = [a for a in agents if a.get("owner_user_id") == p.id]
    return agents


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, p: Principal = Depends(require("agents:read"))):
    agent = service.get_agent(agent_id, p.workspace_id)
    if _owned_only(p) and agent.get("owner_user_id") != p.id:
        raise service.GovernanceError(404, f"Agent '{agent_id}' not found")
    return agent


@router.post("/agents", status_code=201)
async def create_agent(agent: AgentCreate, p: Principal = Depends(require("agents:register"))):
    payload = agent.model_dump()
    if p.kind == "user":
        payload["owner_user_id"] = p.id
        payload.setdefault("accountable_owner", f"user:{p.id}")
        payload["accountable_owner"] = payload["accountable_owner"] or f"user:{p.id}"
    entry, created, credentials = service.create_agent(payload, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [created])
    return {"message": "Agent created successfully", "agent": created, "credentials": credentials}


@router.post("/agents/{agent_id}/rotate-secret")
def rotate_secret(agent_id: str, p: Principal = Depends(require("agents:rotate_secret"))):
    return service.rotate_secret(agent_id, actor=p.actor, workspace_id=p.workspace_id)


@router.patch("/agents/{agent_id}/policy")
async def update_policy(agent_id: str, update: AgentPolicyUpdate, p: Principal = Depends(require("policy:edit"))):
    changes = {field: getattr(update, field) for field in update.model_fields_set if field != "note"}
    if _needs_checker(p):
        base = service.get_agent(agent_id, p.workspace_id)["policy_version"]
        approval = service.request_change(
            "policy_publish", {"op": "update", "changes": changes, "note": update.note, "base_version": base},
            actor=p.actor, workspace_id=p.workspace_id, agent_key=agent_id)
        return JSONResponse({"message": "change request created; an approver must publish it",
                             "approval": approval}, status_code=202)
    entry, agent = service.update_policy(agent_id, changes, update.note, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": "Policy updated", "agent_id": agent_id, "policy_version": agent["policy_version"], "agent": agent}


@router.get("/agents/{agent_id}/policy-history")
def policy_history(agent_id: str, p: Principal = Depends(require("policy:read"))):
    return service.policy_history(agent_id, p.workspace_id)


@router.post("/agents/{agent_id}/policy/rollback/{version}")
async def rollback_policy(agent_id: str, version: int, p: Principal = Depends(require("policy:edit"))):
    if _needs_checker(p):
        base = service.get_agent(agent_id, p.workspace_id)["policy_version"]
        approval = service.request_change("policy_publish", {"op": "rollback", "version": version, "base_version": base},
                                          actor=p.actor, workspace_id=p.workspace_id, agent_key=agent_id)
        return JSONResponse({"message": "rollback requested; an approver must publish it", "approval": approval},
                            status_code=202)
    entry, agent = service.rollback_policy(agent_id, version, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": f"Rolled back to v{version}", "policy_version": agent["policy_version"], "agent": agent}


@router.post("/agents/{agent_id}/reset-spend")
async def reset_spend(agent_id: str, p: Principal = Depends(require("spend:reset"))):
    entry, agent = service.reset_spend(agent_id, actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, [agent])
    return {"message": "Spend reset to 0.00", "agent_id": agent_id}


@router.post("/reset-all-spend")
async def reset_all_spend(p: Principal = Depends(require("spend:reset"))):
    entry, agents = service.reset_all_spend(actor=p.actor, workspace_id=p.workspace_id)
    await ws_manager.publish(entry, agents)
    return {"message": "All agent spend reset to 0.00"}
