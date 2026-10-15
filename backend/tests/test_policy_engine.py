import pytest

from veyrqun.policy_engine import APPROVED, BLOCKED, evaluate_policy


def with_(agent, **overrides):
    return {**agent, **overrides}


@pytest.mark.parametrize(
    "overrides, action, amount, kwargs, expected",
    [
        ({}, "pay", 100.0, {}, (APPROVED, None)),
        ({}, "pay", 0.0, {}, (APPROVED, None)),
        ({}, "pay", 100.0, {"fleet_stopped": True}, (BLOCKED, "fleet_emergency_stop_active")),
        ({"status": "revoked"}, "pay", 1.0, {}, (BLOCKED, "agent_revoked")),
        ({"status": "stopped"}, "pay", 1.0, {}, (BLOCKED, "emergency_stop_active")),
        ({}, "wire_transfer", 1.0, {}, (BLOCKED, "action_not_permitted")),
        ({}, "open_account", 1.0, {}, (BLOCKED, "action_not_in_allowlist")),
        ({"max_per_transaction": 250.0}, "pay", 250.01, {}, (BLOCKED, "transaction_limit_exceeded")),
        ({"max_per_transaction": 250.0}, "pay", 250.0, {}, (APPROVED, None)),
        ({"action_limits": {"refund": 50.0}}, "refund", 60.0, {}, (BLOCKED, "transaction_limit_exceeded")),
        ({"action_limits": {"refund": 50.0}}, "pay", 60.0, {}, (APPROVED, None)),
        ({"rate_limit_per_minute": 3}, "pay", 1.0, {"recent_actions": 3}, (BLOCKED, "rate_limit_exceeded")),
        ({"rate_limit_per_minute": 3}, "pay", 1.0, {"recent_actions": 2}, (APPROVED, None)),
        ({"spend_used_today": 900.0}, "pay", 100.01, {}, (BLOCKED, "spend_cap_exceeded")),
        ({"spend_used_today": 900.0}, "pay", 100.0, {}, (APPROVED, None)),  # exactly at cap is allowed
        ({"spend_used_today": 0.1}, "pay", 999.9, {}, (APPROVED, None)),  # float drift must not block
        ({"spend_cap_daily": 0.0}, "pay", 0.0, {}, (APPROVED, None)),  # zero-value read actions still pass
    ],
)
def test_each_rule(base_agent, overrides, action, amount, kwargs, expected):
    assert evaluate_policy(with_(base_agent, **overrides), action, amount, **kwargs) == expected


def test_precedence_fleet_beats_everything(base_agent):
    agent = with_(base_agent, status="revoked", spend_used_today=5000.0)
    assert evaluate_policy(agent, "wire_transfer", 1e9, fleet_stopped=True) == (BLOCKED, "fleet_emergency_stop_active")


def test_precedence_revoked_beats_stopped_rules(base_agent):
    assert evaluate_policy(with_(base_agent, status="revoked"), "wire_transfer", 1.0)[1] == "agent_revoked"


def test_precedence_blocklist_beats_allowlist(base_agent):
    agent = with_(base_agent, allowed_actions=["pay", "wire_transfer"])
    assert evaluate_policy(agent, "wire_transfer", 1.0)[1] == "action_not_permitted"


def test_precedence_transaction_limit_before_spend_cap(base_agent):
    agent = with_(base_agent, max_per_transaction=10.0, spend_used_today=999.0)
    assert evaluate_policy(agent, "pay", 50.0)[1] == "transaction_limit_exceeded"


def test_evaluation_is_pure(base_agent):
    snapshot = dict(base_agent)
    evaluate_policy(base_agent, "pay", 100.0)
    assert base_agent == snapshot
