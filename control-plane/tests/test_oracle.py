"""The Python reference oracle (descended from v2's evaluate_policy). Rego must agree with it
(test_parity.py), so its own behaviour is pinned here, rule by rule."""

import pytest

from veyrqun.policy.oracle import ALLOW, APPROVAL, BLOCK, REDACT, evaluate

AGENT = {
    "status": "active",
    "allowed_actions": ["pay", "refund"],
    "blocked_actions": ["wire_transfer"],
    "spend_cap_daily_minor": 100_000,
    "max_per_transaction_minor": 25_000,
    "action_limits_minor": {"refund": 5_000},
    "action_daily_caps_minor": {"refund": 8_000},
    "rate_limit_per_minute": 3,
    "approval_threshold_minor": 20_000,
    "redact_data_classes": ["email"],
    "block_data_classes": ["pci_pan"],
}


def inp(action="pay", amount=100, **extra):
    return {"action": action, "amount_minor": amount, **extra}


@pytest.mark.parametrize(
    "agent_over, request_over, state, expected",
    [
        ({}, {}, {}, (ALLOW, None)),
        ({}, {"amount": 0}, {}, (ALLOW, None)),
        ({}, {}, {"fleet_stopped": True}, (BLOCK, "fleet_emergency_stop_active")),
        ({"status": "revoked"}, {}, {}, (BLOCK, "agent_revoked")),
        ({"status": "stopped"}, {}, {}, (BLOCK, "emergency_stop_active")),
        ({}, {"action": "wire_transfer"}, {}, (BLOCK, "action_not_permitted")),
        ({}, {"action": "open_account"}, {}, (BLOCK, "action_not_in_allowlist")),
        ({}, {"data_classes": ["pci_pan"]}, {}, (BLOCK, "data_class_not_permitted")),
        ({}, {"amount": 25_001}, {}, (BLOCK, "transaction_limit_exceeded")),
        ({}, {"action": "refund", "amount": 5_001}, {}, (BLOCK, "transaction_limit_exceeded")),
        ({}, {"signals": {"injection_score": 0.95}}, {}, (BLOCK, "injection_suspected")),
        ({}, {"signals": {"injection_score": None}}, {}, (ALLOW, None)),
        ({}, {"amount": 20_000}, {}, (APPROVAL, "approval_required")),
        ({}, {"amount": 20_000, "approval_granted": True}, {}, (ALLOW, None)),
        ({}, {}, {"recent_actions": 3}, (BLOCK, "rate_limit_exceeded")),
        ({}, {}, {"recent_actions": 2}, (ALLOW, None)),
        ({}, {"amount": 1}, {"spend_minor": 100_000}, (BLOCK, "spend_cap_exceeded")),
        ({}, {"amount": 100}, {"spend_minor": 99_900}, (ALLOW, None)),  # exactly at cap
        ({}, {"action": "refund", "amount": 100}, {"action_spend_minor": 7_950}, (BLOCK, "action_spend_cap_exceeded")),
        ({}, {"data_classes": ["email", "phone"]}, {}, (REDACT, "data_class_redacted")),
        ({"spend_cap_daily_minor": 0}, {"amount": 0}, {}, (ALLOW, None)),
    ],
)
def test_each_rule(agent_over, request_over, state, expected):
    amount = request_over.pop("amount", 100)
    action = request_over.pop("action", "pay")
    outcome, rule, _ = evaluate({**AGENT, **agent_over}, inp(action, amount, **request_over), **state)
    assert (outcome, rule) == expected


def test_unknown_agent_fails_closed():
    assert evaluate(None, inp())[:2] == (BLOCK, "unknown_agent")


def test_fleet_beats_everything_even_for_unknown_agents():
    assert evaluate(None, inp(), fleet_stopped=True)[:2] == (BLOCK, "fleet_emergency_stop_active")


def test_blocklist_beats_allowlist():
    agent = {**AGENT, "allowed_actions": ["pay", "wire_transfer"]}
    assert evaluate(agent, inp("wire_transfer"))[1] == "action_not_permitted"


def test_static_limits_checked_before_counters():
    assert evaluate(AGENT, inp(amount=30_000), spend_minor=100_000, recent_actions=99)[1] == "transaction_limit_exceeded"
