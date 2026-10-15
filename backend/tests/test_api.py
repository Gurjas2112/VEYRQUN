from concurrent.futures import ThreadPoolExecutor

from veyrqun import service


def act(client, agent_id, action, amount=0.0):
    res = client.post("/simulate/action", json={"agent_id": agent_id, "action_type": action, "amount": amount})
    assert res.status_code == 200, res.text
    return res.json()


def test_seeded_fleet(client):
    agents = client.get("/agents").json()
    assert len(agents) == 5
    assert all(a["status"] == "active" and a["policy_version"] == 1 for a in agents)


def test_create_agent_and_duplicate(client):
    payload = {
        "agent_id": "payroll_agent_06",
        "display_name": "Payroll Agent",
        "allowed_actions": ["payroll_run"],
        "blocked_actions": [],
        "spend_cap_daily": 10000,
        "max_per_transaction": 5000,
        "rate_limit_per_minute": 2,
    }
    assert client.post("/agents", json=payload).status_code == 201
    assert client.post("/agents", json=payload).status_code == 409
    assert act(client, "payroll_agent_06", "payroll_run", 4000)["outcome"] == "approved"
    assert act(client, "payroll_agent_06", "payroll_run", 5001)["rule_triggered"] == "transaction_limit_exceeded"


def test_input_validation(client):
    assert client.post("/simulate/action", json={"agent_id": "travel_agent_01", "action_type": "flight_rebook", "amount": -5}).status_code == 422
    assert client.post("/simulate/action", json={"agent_id": "nope", "action_type": "flight_rebook", "amount": 1}).status_code == 404
    assert client.post("/agents", json={"agent_id": "X", "display_name": "x", "spend_cap_daily": 1}).status_code == 422


def test_spend_cap_enforced_and_reset(client):
    assert act(client, "travel_agent_01", "flight_rebook", 600)["outcome"] == "approved"
    assert act(client, "travel_agent_01", "hotel_rebook", 600)["outcome"] == "approved"
    blocked = act(client, "travel_agent_01", "flight_rebook", 400)
    assert blocked["rule_triggered"] == "spend_cap_exceeded"
    assert client.get("/agents/travel_agent_01").json()["spend_used_today"] == 1200
    client.post("/agents/travel_agent_01/reset-spend")
    assert act(client, "travel_agent_01", "flight_rebook", 400)["outcome"] == "approved"


def test_policy_update_takes_effect_immediately_and_bumps_version(client):
    assert act(client, "fee_reversal_02", "wire_transfer", 10)["rule_triggered"] == "action_not_permitted"
    res = client.patch(
        "/agents/fee_reversal_02/policy",
        json={"allowed_actions": ["waive_fee", "wire_transfer"], "blocked_actions": [], "note": "pilot"},
    ).json()
    assert res["policy_version"] == 2
    entry = act(client, "fee_reversal_02", "wire_transfer", 10)
    assert entry["outcome"] == "approved" and entry["policy_version"] == 2


def test_explicit_null_clears_a_limit(client):
    assert act(client, "travel_agent_01", "flight_rebook", 700)["rule_triggered"] == "transaction_limit_exceeded"
    client.patch("/agents/travel_agent_01/policy", json={"max_per_transaction": None})
    assert act(client, "travel_agent_01", "flight_rebook", 700)["outcome"] == "approved"


def test_policy_history_and_rollback(client):
    client.patch("/agents/card_replacement_04/policy", json={"spend_cap_daily": 5})
    assert act(client, "card_replacement_04", "card_issue", 20)["rule_triggered"] == "spend_cap_exceeded"
    rolled = client.post("/agents/card_replacement_04/policy/rollback/1").json()
    assert rolled["policy_version"] == 3
    assert rolled["agent"]["spend_cap_daily"] == 200
    history = client.get("/agents/card_replacement_04/policy-history").json()
    assert [h["version"] for h in history] == [3, 2, 1]
    assert history[0]["note"] == "rollback to v1"
    assert client.post("/agents/card_replacement_04/policy/rollback/99").status_code == 404


def test_rate_limit(client):
    results = [act(client, "balance_check_05", "balance_check")["outcome"] for _ in range(6)]
    assert results == ["approved"] * 4 + ["blocked"] * 2
    assert act(client, "balance_check_05", "balance_check")["rule_triggered"] == "rate_limit_exceeded"


def test_single_agent_stop_and_resume(client):
    client.post("/emergency-stop/credit_limit_03")
    assert act(client, "credit_limit_03", "score_check")["rule_triggered"] == "emergency_stop_active"
    assert act(client, "travel_agent_01", "flight_rebook", 10)["outcome"] == "approved"
    client.post("/resume/credit_limit_03")
    assert act(client, "credit_limit_03", "score_check")["outcome"] == "approved"


def test_fleet_emergency_stop_halts_everyone(client):
    client.post("/emergency-stop")
    assert client.get("/fleet/status").json()["fleet_stopped"] is True
    for agent in client.get("/agents").json():
        assert agent["status"] == "stopped"
        action = agent["allowed_actions"][0]
        assert act(client, agent["agent_id"], action)["rule_triggered"] == "fleet_emergency_stop_active"
    # Resuming a single agent does not bypass the fleet-wide stop.
    client.post("/resume/travel_agent_01")
    assert act(client, "travel_agent_01", "flight_rebook", 1)["rule_triggered"] == "fleet_emergency_stop_active"
    client.post("/resume-all")
    assert act(client, "travel_agent_01", "flight_rebook", 1)["outcome"] == "approved"


def test_revocation_survives_fleet_resume(client):
    client.post("/agents/travel_agent_01/revoke", json={"reason": "suspected prompt injection"})
    agent = client.get("/agents/travel_agent_01").json()
    assert agent["status"] == "revoked" and agent["status_reason"] == "suspected prompt injection"
    assert act(client, "travel_agent_01", "flight_rebook", 1)["rule_triggered"] == "agent_revoked"
    client.post("/emergency-stop")
    client.post("/resume-all")
    assert client.get("/agents/travel_agent_01").json()["status"] == "revoked"
    assert client.post("/resume/travel_agent_01").status_code == 409
    client.post("/agents/travel_agent_01/reinstate")
    assert act(client, "travel_agent_01", "flight_rebook", 1)["outcome"] == "approved"


def test_concurrent_actions_never_overshoot_cap(client):
    # 50 parallel $100 requests against a $1500 cap with a $600 per-tx limit: exactly 15 may pass.
    client.patch("/agents/travel_agent_01/policy", json={"rate_limit_per_minute": None})
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(lambda _: service.attempt_action("travel_agent_01", "flight_rebook", 100.0)[0]["outcome"], range(50)))
    assert outcomes.count("approved") == 15
    assert client.get("/agents/travel_agent_01").json()["spend_used_today"] == 1500.0
    assert client.get("/audit-log/verify").json()["valid"] is True


def test_audit_log_filters(client):
    act(client, "travel_agent_01", "flight_rebook", 10)
    act(client, "travel_agent_01", "credit_limit_increase", 10)
    act(client, "fee_reversal_02", "waive_fee", 10)
    blocked = client.get("/audit-log", params={"agent_id": "travel_agent_01", "outcome": "blocked"}).json()
    assert len(blocked) == 1 and blocked[0]["rule_triggered"] == "action_not_permitted"
    assert len(client.get("/audit-log", params={"limit": 2}).json()) == 2


def test_metrics_endpoints(client):
    act(client, "travel_agent_01", "flight_rebook", 10)
    act(client, "travel_agent_01", "credit_limit_increase", 10)
    m = client.get("/metrics").json()
    assert m["approved_total"] == 1 and m["blocked_total"] == 1
    assert m["blocked_by_rule"] == {"action_not_permitted": 1}
    assert m["decision_latency"]["samples"] == 2
    prom = client.get("/metrics/prometheus").text
    assert 'veyrqun_decisions_total{outcome="approved"} 1' in prom
    assert "veyrqun_fleet_stopped 0" in prom


def test_websocket_streams_decisions(client):
    with client.websocket_connect("/ws/live-feed") as ws:
        act(client, "travel_agent_01", "flight_rebook", 10)
        msg = ws.receive_json()
        assert msg["agent_id"] == "travel_agent_01" and msg["outcome"] == "approved"
        assert msg["_agent_snapshot"]["spend_used_today"] == 10
