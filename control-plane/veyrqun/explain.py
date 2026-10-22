"""Plain-English reasons for every rule_id the policy engine, the counters or the gateway can return.

The decision itself is always the deterministic rule_id. This text only explains it to a person.
"""

from __future__ import annotations

REASONS: dict[str, str] = {
    "": "Allowed: the action is on the agent's allowlist and within every limit.",
    "unknown_agent": "Blocked: this agent isn't registered, and unknown agents are denied by default.",
    "policy_undefined": "Blocked: no policy matched this request, so it is denied by default.",
    "data_class_redacted": "Allowed with redaction: sensitive data was masked before it left the gateway.",
    "fleet_emergency_stop_active": "Blocked: an operator has stopped the whole fleet (kill switch).",
    "agent_revoked": "Blocked: this agent's access has been revoked.",
    "emergency_stop_active": "Blocked: an operator has stopped this agent.",
    "action_not_permitted": "Blocked: this action is explicitly forbidden for the agent.",
    "action_not_in_allowlist": "Blocked: the action isn't on the agent's allowlist (default deny).",
    "data_class_not_permitted": "Blocked: the request contains data this agent may never send (for example a card number).",
    "transaction_limit_exceeded": "Blocked: the amount is over the agent's per-transaction limit.",
    "injection_suspected": "Blocked: the text looks like a prompt-injection attempt (signal over the policy threshold).",
    "rate_limit_exceeded": "Blocked: the agent is calling too often (per-minute rate limit).",
    "spend_cap_exceeded": "Blocked: this would take the agent over its daily spend cap.",
    "action_spend_cap_exceeded": "Blocked: this would exceed the daily cap for this specific action.",
    "state_store_unavailable": "Blocked: the spend counters are unreachable, so the gateway fails closed.",
    "approval_required": "Paused: the amount is over the approval threshold, so a human must approve it first.",
    "redact": "Allowed with redaction: sensitive data was masked before it left the gateway.",
    "response_policy_violation": "Stopped: the model's answer contained data this agent may not receive.",
    "audit_backlog": "Refused: the audit pipeline is behind and strict mode refuses new actions.",
    "policy_unavailable": "Refused: no fresh signed policy is loaded, so the gateway fails closed.",
    "approval_not_usable": "Refused: that approval isn't approved, or it was granted for a different action or amount.",
    "llm_budget_exhausted": "Refused: the daily model budget for this deployment is used up.",
}


def reason(outcome: str | None, rule_id: str | None, error_type: str | None = None) -> str:
    if outcome == "require_approval":
        return REASONS["approval_required"]
    if outcome == "redact":
        return REASONS["redact"]
    for key in (rule_id, error_type):
        if key and key in REASONS:
            return REASONS[key]
    if outcome == "allow":
        return REASONS[""]
    return f"Refused ({rule_id or error_type or outcome or 'unknown'})."
