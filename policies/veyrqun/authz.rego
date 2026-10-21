# VEYRQUN authorization policy.
#
# Static, ordered, first-match-wins rules. Limits that depend on live counters (daily spend,
# per-action daily spend, rate) are returned in `limits` and enforced atomically by the Redis
# Lua script in policies/lua/counters.lua, which applies rate -> spend -> action-spend order.
#
# All money is integer minor units (cents/paise). Unknown agents, missing data and any rule
# that is undefined fall through to `default decision`, which blocks (fail-closed).
package veyrqun.authz

import rego.v1

default decision := {
	"outcome": "block",
	"rule_id": "policy_undefined",
	"redactions": [],
	"limits": {},
	"mode": "enforce",
	"policy_version": 0,
}

agent := data.policy_data.agents[input.agent_id]

data_classes := object.get(input, "data_classes", [])

signals := object.get(input, "signals", {})

fleet_stopped if input.fleet_state.stopped == true

fleet_stopped if data.policy_data.fleet.stopped == true

injection_threshold := object.get(object.get(data.policy_data, "settings", {}), "injection_block_threshold", 1.01)

# --- ordered block rules -------------------------------------------------------------------

block_rule := "fleet_emergency_stop_active" if {
	fleet_stopped
} else := "unknown_agent" if {
	not agent
} else := "agent_revoked" if {
	agent.status == "revoked"
} else := "emergency_stop_active" if {
	agent.status == "stopped"
} else := "action_not_permitted" if {
	input.action in agent.blocked_actions
} else := "action_not_in_allowlist" if {
	not input.action in agent.allowed_actions
} else := "data_class_not_permitted" if {
	some c in data_classes
	c in object.get(agent, "block_data_classes", [])
} else := "transaction_limit_exceeded" if {
	agent.max_per_transaction_minor != null
	input.amount_minor > agent.max_per_transaction_minor
} else := "transaction_limit_exceeded" if {
	cap := object.get(agent, "action_limits_minor", {})[input.action]
	input.amount_minor > cap
} else := "injection_suspected" if {
	# Models only emit signals; this rule decides. A null/missing score never blocks here.
	score := signals.injection_score
	is_number(score)
	score >= injection_threshold
} else := ""

needs_approval if {
	agent.approval_threshold_minor != null
	input.amount_minor >= agent.approval_threshold_minor
	not input.approval_granted == true
}

redactions := sort([c | some c in data_classes; c in object.get(agent, "redact_data_classes", [])])

limits := {
	"spend_cap_minor": agent.spend_cap_daily_minor,
	"rate_limit_per_minute": agent.rate_limit_per_minute,
	"action_daily_cap_minor": object.get(object.get(agent, "action_daily_caps_minor", {}), input.action, null),
}

base := {
	"mode": object.get(agent, "mode", "enforce"),
	"policy_version": object.get(agent, "policy_version", 0),
}

# --- decision (the four bodies are mutually exclusive) ------------------------------------

decision := object.union(base, {
	"outcome": "block",
	"rule_id": block_rule,
	"redactions": [],
	"limits": {},
}) if {
	block_rule != ""
	agent
}

decision := {
	"outcome": "block",
	"rule_id": block_rule,
	"redactions": [],
	"limits": {},
	"mode": "enforce",
	"policy_version": 0,
} if {
	block_rule != ""
	not agent
}

decision := object.union(base, {
	"outcome": "require_approval",
	"rule_id": "approval_required",
	"redactions": redactions,
	"limits": limits,
}) if {
	block_rule == ""
	needs_approval
}

decision := object.union(base, {
	"outcome": "redact",
	"rule_id": "data_class_redacted",
	"redactions": redactions,
	"limits": limits,
}) if {
	block_rule == ""
	not needs_approval
	count(redactions) > 0
}

decision := object.union(base, {
	"outcome": "allow",
	"rule_id": null,
	"redactions": [],
	"limits": limits,
}) if {
	block_rule == ""
	not needs_approval
	count(redactions) == 0
}
