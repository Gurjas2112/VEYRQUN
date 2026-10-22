"""One LangGraph state machine for every fleet agent: model -> tools -> model ... -> done.

There is no governance logic here. Every model and tool call goes through the gateway, which decides. The graph
only routes on the gateway's answer:
  * refused  -> record it, tell the model (tool) or stop (model), never retry around it;
  * approval -> pause the run with `interrupt()` until a human decides in the control plane, then retry the same
                call with the approval id (the gateway binds the approval to agent, action and amount).

Human-in-the-loop safety: LangGraph re-runs an interrupted node from the top when it resumes. So the node that
calls `interrupt()` (`await_approval`) has no side effects, and each tool call is its own node execution, which
means no tool runs twice.
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .client import GatewayClient, Outcome

MAX_MODEL_TURNS = 4


class AgentState(TypedDict, total=False):
    messages: list[dict]                       # OpenAI-format conversation
    queue: list[dict]                          # tool calls from the last model turn, not yet executed
    pending: dict | None                       # tool call waiting for a human approval
    approval: dict | None                      # the human's decision for `pending`
    turns: int
    final: str | None
    trace: Annotated[list[dict], operator.add]  # every governed call: what was asked, what the gateway said


def _record(step: str, target: str, out: Outcome, **extra: Any) -> dict:
    return {"step": step, "target": target, "kind": out.kind, "status": out.status, "rule_id": out.rule_id,
            "decision_id": out.decision_id, **extra}


def build_graph(client: GatewayClient, tools: list[dict] | None = None, checkpointer=None):
    tool_defs = tools if tools is not None else client.list_tools()

    def model(state: AgentState) -> dict:
        out = client.chat(state["messages"], tool_defs or None)
        rec = _record("model", client.model, out)
        if out.kind != "ok":
            return {"final": f"Stopped: the gateway refused the model call ({out.rule_id}).", "trace": [rec],
                    "queue": []}
        msg = out.body["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        update: dict[str, Any] = {"messages": [*state["messages"], {k: v for k, v in msg.items() if v is not None}],
                                  "turns": state.get("turns", 0) + 1, "trace": [rec], "queue": calls}
        if not calls:
            update["final"] = msg.get("content") or ""
        return update

    def tools_node(state: AgentState) -> dict:
        pending, approval = state.get("pending"), state.get("approval")
        if pending is not None:  # resuming after a human decision
            call, queue = pending, state.get("queue", [])
        else:
            call, queue = state["queue"][0], state["queue"][1:]
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        if pending is not None and approval and not approval.get("approved"):
            content = f"REJECTED by a human approver: {approval.get('reason') or 'no reason given'}"
            rec = {"step": "tool", "target": name, "kind": "rejected", "status": None, "rule_id": None,
                   "decision_id": None, "approval_id": approval.get("approval_id")}
            return _tool_result(state, call, content, queue, rec)
        approval_id = approval.get("approval_id") if pending is not None and approval else None
        out = client.call_tool(name, args, approval_id=approval_id)
        rec = _record("tool", name, out, **({"approval_id": approval_id} if approval_id else {}))
        if out.kind == "approval":
            # Don't drop the call: park it and wait for a human (next node interrupts).
            return {"pending": call, "queue": queue, "approval": {"approval_id": out.approval_id},
                    "trace": [{**rec, "approval_id": out.approval_id}]}
        if out.kind == "ok":
            content = "".join(p.get("text", "") for p in (out.body.get("result") or {}).get("content", []))
        else:
            content = f"BLOCKED by policy ({out.rule_id}). Do not retry this action."
        return _tool_result(state, call, content, queue, rec)

    def await_approval(state: AgentState) -> dict:
        # No side effects before interrupt(): this node is re-run from the top on resume.
        approval_id = state["approval"]["approval_id"]
        resume = interrupt({"approval_id": approval_id, "tool": state["pending"]["function"]["name"],
                            "arguments": state["pending"]["function"].get("arguments")}) or {}
        # The resume value only wakes the run. The decision is read from the control plane (via the gateway),
        # so nobody can approve an action just by resuming the graph with {"approved": true}.
        status = client.approval_status(approval_id)
        return {"approval": {"approval_id": approval_id, "approved": status == "approved", "status": status,
                             "reason": resume.get("reason") if isinstance(resume, dict) else None}}

    def route_after_model(state: AgentState) -> str:
        if state.get("final") is not None:
            return END
        return "tools" if state.get("queue") else END

    def route_after_tools(state: AgentState) -> str:
        if state.get("pending") is not None and state.get("approval") and "approved" not in state["approval"]:
            return "await_approval"
        if state.get("queue"):
            return "tools"
        return "model" if state.get("turns", 0) < MAX_MODEL_TURNS else END

    g = StateGraph(AgentState)
    g.add_node("model", model)
    g.add_node("tools", tools_node)
    g.add_node("await_approval", await_approval)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route_after_model, ["tools", END])
    g.add_conditional_edges("tools", route_after_tools, ["tools", "await_approval", "model", END])
    g.add_edge("await_approval", "tools")
    return g.compile(checkpointer=checkpointer)


def _tool_result(state: AgentState, call: dict, content: str, queue: list[dict], rec: dict) -> dict:
    return {"messages": [*state["messages"], {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}],
            "queue": queue, "pending": None, "approval": None, "trace": [rec]}


def initial_state(system_prompt: str, task: str) -> AgentState:
    return {"messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": task}],
            "queue": [], "pending": None, "approval": None, "turns": 0, "final": None, "trace": []}
