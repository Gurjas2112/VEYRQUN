package veyrqun.authz_test

import data.veyrqun.authz

base_agent := {
	"status": "active",
	"allowed_actions": ["pay", "refund", "wire_transfer"],
	"blocked_actions": ["wire_transfer"],
	"spend_cap_daily_minor": 100000,
	"max_per_transaction_minor": 25000,
	"action_limits_minor": {"refund": 5000},
	"action_daily_caps_minor": {"refund": 20000},
	"rate_limit_per_minute": 10,
	"approval_threshold_minor": 20000,
	"redact_data_classes": ["email"],
	"block_data_classes": ["pci_pan"],
	"mode": "enforce",
	"policy_version": 7,
}

pd(agent) := {"agents": {"a1": agent}, "fleet": {"stopped": false}, "settings": {"injection_block_threshold": 0.9}}

decide(agent, inp) := d if {
	d := authz.decision with data.policy_data as pd(agent) with input as object.union({"agent_id": "a1"}, inp)
}

test_allow_within_policy if {
	d := decide(base_agent, {"action": "pay", "amount_minor": 1000})
	d.outcome == "allow"
	d.rule_id == null
	d.policy_version == 7
	d.limits == {"spend_cap_minor": 100000, "rate_limit_per_minute": 10, "action_daily_cap_minor": null}
}

test_unknown_agent_fails_closed if {
	d := authz.decision with data.policy_data as pd(base_agent) with input as {"agent_id": "ghost", "action": "pay", "amount_minor": 1}
	d.outcome == "block"
	d.rule_id == "unknown_agent"
}

test_missing_policy_data_fails_closed if {
	d := authz.decision with data.policy_data as {} with input as {"agent_id": "a1", "action": "pay", "amount_minor": 1}
	d.outcome == "block"
}

test_fleet_stop_from_data if {
	d := authz.decision with data.policy_data as {"agents": {"a1": base_agent}, "fleet": {"stopped": true}}
		with input as {"agent_id": "a1", "action": "pay", "amount_minor": 1}
	d.rule_id == "fleet_emergency_stop_active"
}

test_fleet_stop_from_input_beats_everything if {
	revoked := object.union(base_agent, {"status": "revoked"})
	d := decide(revoked, {"action": "wire_transfer", "amount_minor": 999999999, "fleet_state": {"stopped": true}})
	d.rule_id == "fleet_emergency_stop_active"
}

test_fleet_stop_blocks_unknown_agents_too if {
	d := authz.decision with data.policy_data as pd(base_agent)
		with input as {"agent_id": "ghost", "action": "pay", "amount_minor": 1, "fleet_state": {"stopped": true}}
	d.rule_id == "fleet_emergency_stop_active"
}

test_revoked if {
	d := decide(object.union(base_agent, {"status": "revoked"}), {"action": "pay", "amount_minor": 1})
	d.rule_id == "agent_revoked"
}

test_stopped if {
	d := decide(object.union(base_agent, {"status": "stopped"}), {"action": "pay", "amount_minor": 1})
	d.rule_id == "emergency_stop_active"
}

test_blocklist_beats_allowlist if {
	d := decide(base_agent, {"action": "wire_transfer", "amount_minor": 1})
	d.rule_id == "action_not_permitted"
}

test_default_deny_not_in_allowlist if {
	d := decide(base_agent, {"action": "open_account", "amount_minor": 0})
	d.rule_id == "action_not_in_allowlist"
}

test_blocked_data_class if {
	d := decide(base_agent, {"action": "pay", "amount_minor": 1, "data_classes": ["pci_pan", "email"]})
	d.rule_id == "data_class_not_permitted"
}

test_per_transaction_limit_boundary if {
	decide(base_agent, {"action": "pay", "amount_minor": 25000}).outcome == "require_approval"
	decide(base_agent, {"action": "pay", "amount_minor": 25001}).rule_id == "transaction_limit_exceeded"
}

test_per_action_limit if {
	decide(base_agent, {"action": "refund", "amount_minor": 5000}).outcome == "allow"
	decide(base_agent, {"action": "refund", "amount_minor": 5001}).rule_id == "transaction_limit_exceeded"
	decide(base_agent, {"action": "pay", "amount_minor": 5001}).outcome == "allow"
}

test_null_per_transaction_limit_means_none if {
	agent := object.union(base_agent, {"max_per_transaction_minor": null, "approval_threshold_minor": null})
	decide(agent, {"action": "pay", "amount_minor": 99999999}).outcome == "allow"
}

test_require_approval_at_threshold if {
	d := decide(base_agent, {"action": "pay", "amount_minor": 20000})
	d.outcome == "require_approval"
	d.rule_id == "approval_required"
	decide(base_agent, {"action": "pay", "amount_minor": 19999}).outcome == "allow"
}

test_granted_approval_proceeds if {
	decide(base_agent, {"action": "pay", "amount_minor": 20000, "approval_granted": true}).outcome == "allow"
}

test_redact_outcome if {
	d := decide(base_agent, {"action": "pay", "amount_minor": 1, "data_classes": ["email", "phone"]})
	d.outcome == "redact"
	d.redactions == ["email"]
	d.limits.spend_cap_minor == 100000
}

test_injection_signal_blocks_only_above_threshold if {
	decide(base_agent, {"action": "pay", "amount_minor": 1, "signals": {"injection_score": 0.95}}).rule_id == "injection_suspected"
	decide(base_agent, {"action": "pay", "amount_minor": 1, "signals": {"injection_score": 0.5}}).outcome == "allow"
}

test_null_injection_signal_never_blocks if {
	decide(base_agent, {"action": "pay", "amount_minor": 1, "signals": {"injection_score": null}}).outcome == "allow"
}

test_action_daily_cap_returned_as_limit if {
	decide(base_agent, {"action": "refund", "amount_minor": 1}).limits.action_daily_cap_minor == 20000
}

test_observe_mode_is_reported if {
	decide(object.union(base_agent, {"mode": "observe"}), {"action": "wire_transfer", "amount_minor": 1}).mode == "observe"
}
