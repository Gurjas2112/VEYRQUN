"""Deterministic, side-effect-free policy evaluation.

Kept pure (no DB, no clock) so it can be unit-tested exhaustively and benchmarked in
isolation; the service layer supplies fleet state and the agent's recent action count.
"""

from typing import Optional, Tuple

APPROVED = "approved"
BLOCKED = "blocked"

# Evaluation order — the first rule that fires wins.
RULES = (
    "fleet_emergency_stop_active",
    "agent_revoked",
    "emergency_stop_active",
    "action_not_permitted",
    "action_not_in_allowlist",
    "transaction_limit_exceeded",
    "rate_limit_exceeded",
    "spend_cap_exceeded",
)


def evaluate_policy(
    agent: dict,
    action_type: str,
    amount: float,
    fleet_stopped: bool = False,
    recent_actions: int = 0,
) -> Tuple[str, Optional[str]]:
    if fleet_stopped:
        return BLOCKED, "fleet_emergency_stop_active"
    status = agent["status"]
    if status == "revoked":
        return BLOCKED, "agent_revoked"
    if status == "stopped":
        return BLOCKED, "emergency_stop_active"
    if action_type in agent["blocked_actions"]:
        return BLOCKED, "action_not_permitted"
    if action_type not in agent["allowed_actions"]:
        return BLOCKED, "action_not_in_allowlist"

    per_tx = agent.get("max_per_transaction")
    if per_tx is not None and amount > per_tx:
        return BLOCKED, "transaction_limit_exceeded"
    action_cap = (agent.get("action_limits") or {}).get(action_type)
    if action_cap is not None and amount > action_cap:
        return BLOCKED, "transaction_limit_exceeded"

    rate_limit = agent.get("rate_limit_per_minute")
    if rate_limit is not None and recent_actions >= rate_limit:
        return BLOCKED, "rate_limit_exceeded"

    # Round to cents so float drift can't block an action that lands exactly on the cap.
    if round(agent["spend_used_today"] + amount, 2) > agent["spend_cap_daily"]:
        return BLOCKED, "spend_cap_exceeded"
    return APPROVED, None
