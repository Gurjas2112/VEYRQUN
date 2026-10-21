"""Reference implementation of the full decision (static rules + live counters), in plain Python.

Not used to serve traffic. It is the independent oracle the Rego policy (+ Lua counters) must
agree with on randomized inputs (tests/test_parity.py). It descends from the v2 `evaluate_policy`
and keeps its rule order and rule_id strings. All money is integer minor units.
"""

from __future__ import annotations

ALLOW, BLOCK, REDACT, APPROVAL = "allow", "block", "redact", "require_approval"

RULE_ORDER = (
    "fleet_emergency_stop_active",
    "unknown_agent",
    "agent_revoked",
    "emergency_stop_active",
    "action_not_permitted",
    "action_not_in_allowlist",
    "data_class_not_permitted",
    "transaction_limit_exceeded",
    "injection_suspected",
    "approval_required",
    "rate_limit_exceeded",
    "spend_cap_exceeded",
    "action_spend_cap_exceeded",
)


def evaluate(
    agent: dict | None,
    inp: dict,
    *,
    fleet_stopped: bool = False,
    spend_minor: int = 0,
    action_spend_minor: int = 0,
    recent_actions: int = 0,
    injection_threshold: float = 0.9,
) -> tuple[str, str | None, list[str]]:
    amount = inp["amount_minor"]
    action = inp["action"]
    classes = inp.get("data_classes", [])
    if fleet_stopped or (inp.get("fleet_state") or {}).get("stopped") is True:
        return BLOCK, "fleet_emergency_stop_active", []
    if agent is None:
        return BLOCK, "unknown_agent", []
    if agent["status"] == "revoked":
        return BLOCK, "agent_revoked", []
    if agent["status"] == "stopped":
        return BLOCK, "emergency_stop_active", []
    if action in agent["blocked_actions"]:
        return BLOCK, "action_not_permitted", []
    if action not in agent["allowed_actions"]:
        return BLOCK, "action_not_in_allowlist", []
    if any(c in agent.get("block_data_classes", []) for c in classes):
        return BLOCK, "data_class_not_permitted", []
    per_tx = agent.get("max_per_transaction_minor")
    if per_tx is not None and amount > per_tx:
        return BLOCK, "transaction_limit_exceeded", []
    action_cap = (agent.get("action_limits_minor") or {}).get(action)
    if action_cap is not None and amount > action_cap:
        return BLOCK, "transaction_limit_exceeded", []
    score = (inp.get("signals") or {}).get("injection_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= injection_threshold:
        return BLOCK, "injection_suspected", []
    threshold = agent.get("approval_threshold_minor")
    if threshold is not None and amount >= threshold and inp.get("approval_granted") is not True:
        return APPROVAL, "approval_required", sorted(c for c in classes if c in agent.get("redact_data_classes", []))
    rate = agent.get("rate_limit_per_minute")
    if rate is not None and recent_actions >= rate:
        return BLOCK, "rate_limit_exceeded", []
    cap = agent.get("spend_cap_daily_minor")
    if cap is not None and spend_minor + amount > cap:
        return BLOCK, "spend_cap_exceeded", []
    daily_action_cap = (agent.get("action_daily_caps_minor") or {}).get(action)
    if daily_action_cap is not None and action_spend_minor + amount > daily_action_cap:
        return BLOCK, "action_spend_cap_exceeded", []
    redactions = sorted({c for c in classes if c in agent.get("redact_data_classes", [])})
    if redactions:
        return REDACT, "data_class_redacted", redactions
    return ALLOW, None, []
