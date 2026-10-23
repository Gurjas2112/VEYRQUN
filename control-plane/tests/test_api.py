"""v2 behaviour, ported to the v3 stack (OPA-WASM policy, Redis counters, Postgres ledger)."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from veyrqun import service


def act(client, agent_id, action, amount="0", **extra):
    res = client.post("/simulate/action", json={"agent_id": agent_id, "action_type": action, "amount": str(amount), **extra})
    assert res.status_code in (200, 202), res.text
    return res.json()


def test_seeded_fleet(client):
    agents = client.get("/agents").json()
    assert len(agents) == 5
    assert all(a["status"] == "active" and a["policy_version"] == 1 for a in agents)
    assert agents[0]["spend_cap_daily"] == "1500.00" and agents[0]["spend_used_today"] == "0.00"


def test_create_agent_returns_credentials_once_and_rejects_duplicates(client):
    payload = {
        "agent_id": "payroll_agent_06",
        "display_name": "Payroll Agent",
        "allowed_actions": ["payroll_run"],
        "blocked_actions": [],
        "spend_cap_daily": "10000",
        "max_per_transaction": "5000",
        "rate_limit_per_minute": 2,
    }
    res = client.post("/agents", json=payload)
    assert res.status_code == 201
    creds = res.json()["credentials"]
    assert creds["client_id"] == "default:payroll_agent_06" and creds["client_secret"].startswith("vqs_")
    assert client.post("/agents", json=payload).status_code == 409
    assert act(client, "payroll_agent_06", "payroll_run", "4000")["outcome"] == "allow"
    assert act(client, "payroll_agent_06", "payroll_run", "5001")["rule_id"] == "transaction_limit_exceeded"


def test_input_validation(client):
    negative = {"agent_id": "travel_agent_01", "action_type": "flight_rebook", "amount": "-5"}
    assert client.post("/simulate/action", json=negative).status_code == 422
    too_precise = {"agent_id": "travel_agent_01", "action_type": "flight_rebook", "amount": "1.001"}
    assert client.post("/simulate/action", json=too_precise).status_code == 422
    extra_field = {"agent_id": "travel_agent_01", "action_type": "flight_rebook", "amount": "1", "admin": True}
    assert client.post("/simulate/action", json=extra_field).status_code == 422
    assert client.post("/agents", json={"agent_id": "X", "display_name": "x", "spend_cap_daily": 1}).status_code == 422


def test_unknown_agent_is_blocked_and_audited(client):
    entry = act(client, "ghost_agent", "flight_rebook", "1")
    assert entry["outcome"] == "block" and entry["rule_id"] == "unknown_agent"


def test_policy_business_rules_rejected(client):
    both = {"agent_id": "x_agent", "display_name": "X", "allowed_actions": ["pay"], "blocked_actions": ["pay"],
            "spend_cap_daily": "10"}
    assert client.post("/agents", json=both).status_code == 422
    per_tx = {"agent_id": "y_agent", "display_name": "Y", "spend_cap_daily": "10", "max_per_transaction": "11"}
    assert client.post("/agents", json=per_tx).status_code == 422
    assert client.patch("/agents/card_replacement_04/policy", json={"spend_cap_daily": "5"}).status_code == 422


def test_spend_cap_enforced_and_reset(client):
    assert act(client, "travel_agent_01", "flight_rebook", "600")["outcome"] == "allow"
    assert act(client, "travel_agent_01", "hotel_rebook", "600")["outcome"] == "allow"
    assert act(client, "travel_agent_01", "flight_rebook", "400")["rule_id"] == "spend_cap_exceeded"
    assert client.get("/agents/travel_agent_01").json()["spend_used_today"] == "1200.00"
    client.post("/agents/travel_agent_01/reset-spend")
    assert act(client, "travel_agent_01", "flight_rebook", "400")["outcome"] == "allow"


def test_cap_reached_exactly_is_allowed(client):
    for amount in ("600", "600", "300"):
        assert act(client, "travel_agent_01", "flight_rebook", amount)["outcome"] == "allow"
    assert act(client, "travel_agent_01", "flight_rebook", "0.01")["rule_id"] == "spend_cap_exceeded"


def test_policy_update_takes_effect_immediately_and_bumps_version(client):
    assert act(client, "fee_reversal_02", "wire_transfer", "10")["rule_id"] == "action_not_permitted"
    res = client.patch("/agents/fee_reversal_02/policy",
                       json={"allowed_actions": ["waive_fee", "wire_transfer"], "blocked_actions": [], "note": "pilot"}).json()
    assert res["policy_version"] == 2
    entry = act(client, "fee_reversal_02", "wire_transfer", "10")
    assert entry["outcome"] == "allow" and entry["policy_version"] == 2


def test_explicit_null_clears_a_limit(client):
    assert act(client, "travel_agent_01", "flight_rebook", "700")["rule_id"] == "transaction_limit_exceeded"
    client.patch("/agents/travel_agent_01/policy", json={"max_per_transaction": None})
    assert act(client, "travel_agent_01", "flight_rebook", "700")["outcome"] == "allow"


def test_policy_history_and_rollback(client):
    client.patch("/agents/card_replacement_04/policy", json={"spend_cap_daily": "50", "max_per_transaction": "20"})
    assert act(client, "card_replacement_04", "card_issue", "25")["rule_id"] == "transaction_limit_exceeded"
    rolled = client.post("/agents/card_replacement_04/policy/rollback/1").json()
    assert rolled["policy_version"] == 3
    assert rolled["agent"]["spend_cap_daily"] == "200.00"
    history = client.get("/agents/card_replacement_04/policy-history").json()
    assert [h["version"] for h in history] == [3, 2, 1]
    assert history[0]["note"] == "rollback to v1"
    assert client.post("/agents/card_replacement_04/policy/rollback/99").status_code == 404


def test_rate_limit(client):
    results = [act(client, "balance_check_05", "balance_check")["outcome"] for _ in range(6)]
    assert results == ["allow"] * 4 + ["block"] * 2
    assert act(client, "balance_check_05", "balance_check")["rule_id"] == "rate_limit_exceeded"


def test_single_agent_stop_and_resume(client):
    client.post("/emergency-stop/credit_limit_03")
    assert act(client, "credit_limit_03", "score_check")["rule_id"] == "emergency_stop_active"
    assert act(client, "travel_agent_01", "flight_rebook", "10")["outcome"] == "allow"
    client.post("/resume/credit_limit_03")
    assert act(client, "credit_limit_03", "score_check")["outcome"] == "allow"


def test_fleet_emergency_stop_halts_everyone(client):
    client.post("/emergency-stop")
    assert client.get("/fleet/status").json()["fleet_stopped"] is True
    for agent in client.get("/agents").json():
        assert agent["status"] == "stopped"
        assert act(client, agent["agent_id"], agent["allowed_actions"][0])["rule_id"] == "fleet_emergency_stop_active"
    client.post("/resume/travel_agent_01")
    assert act(client, "travel_agent_01", "flight_rebook", "1")["rule_id"] == "fleet_emergency_stop_active"
    client.post("/resume-all")
    assert act(client, "travel_agent_01", "flight_rebook", "1")["outcome"] == "allow"


def test_revocation_survives_fleet_resume(client):
    client.post("/agents/travel_agent_01/revoke", json={"reason": "suspected prompt injection"})
    agent = client.get("/agents/travel_agent_01").json()
    assert agent["status"] == "revoked" and agent["status_reason"] == "suspected prompt injection"
    assert act(client, "travel_agent_01", "flight_rebook", "1")["rule_id"] == "agent_revoked"
    client.post("/emergency-stop")
    client.post("/resume-all")
    assert client.get("/agents/travel_agent_01").json()["status"] == "revoked"
    assert client.post("/resume/travel_agent_01").status_code == 409
    client.post("/agents/travel_agent_01/reinstate")
    assert act(client, "travel_agent_01", "flight_rebook", "1")["outcome"] == "allow"


def test_concurrent_actions_never_overshoot_cap(client):
    # 50 parallel $100 requests against a $1,500 cap: Redis Lua makes check+increment atomic,
    # so exactly 15 pass, with no global lock in the process.
    client.patch("/agents/travel_agent_01/policy", json={"rate_limit_per_minute": None})
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(
            lambda _: service.attempt_action("travel_agent_01", "flight_rebook", Decimal("100"))[0]["outcome"], range(50)))
    assert outcomes.count("allow") == 15
    assert client.get("/agents/travel_agent_01").json()["spend_used_today"] == "1500.00"
    assert client.get("/audit-log/verify").json()["valid"] is True


def test_redaction_and_blocked_data_classes(client):
    entry = act(client, "fee_reversal_02", "waive_fee", "10", data_classes=["email"])
    assert entry["outcome"] == "redact" and entry["redactions"] == ["email"]
    blocked = act(client, "credit_limit_03", "score_check", "0", data_classes=["pci_pan"])
    assert blocked["rule_id"] == "data_class_not_permitted"


def test_audit_log_filters_and_keyset_pagination(client):
    act(client, "travel_agent_01", "flight_rebook", "10")
    act(client, "travel_agent_01", "credit_limit_increase", "10")
    act(client, "fee_reversal_02", "waive_fee", "10")
    blocked = client.get("/audit-log", params={"agent_id": "travel_agent_01", "outcome": "block"}).json()
    assert len(blocked) == 1 and blocked[0]["rule_id"] == "action_not_permitted"
    page1 = client.get("/audit-log", params={"limit": 2}).json()
    page2 = client.get("/audit-log", params={"limit": 2, "before_seq": page1[-1]["seq"]}).json()
    assert len(page1) == 2 and page2 and page2[0]["seq"] < page1[-1]["seq"]


def test_metrics_endpoints(client):
    act(client, "travel_agent_01", "flight_rebook", "10")
    act(client, "travel_agent_01", "credit_limit_increase", "10")
    m = client.get("/metrics").json()
    assert m["decisions_total"] == 2 and m["blocked_by_rule"] == {"action_not_permitted": 1}
    prom = client.get("/metrics/prometheus").text
    assert "veyrqun_fleet_stopped 0" in prom


def test_websocket_streams_decisions(client):
    with client.websocket_connect("/ws/live-feed") as ws:
        act(client, "travel_agent_01", "flight_rebook", "10")
        msg = ws.receive_json()
        assert msg["agent_id"] == "travel_agent_01" and msg["outcome"] == "allow"
        assert msg["_agent_snapshot"]["spend_used_today"] == "10.00"


def test_token_endpoint_locks_out_after_repeated_bad_secrets(anon_client):
    from veyrqun import token_guard

    creds = service.create_agent({"agent_id": "lockout_agent", "display_name": "x", "allowed_actions": ["a"],
                                  "blocked_actions": [], "spend_cap_daily": Decimal("1")})[2]
    bad = {"grant_type": "client_credentials", "client_id": creds["client_id"], "client_secret": "wrong"}
    for _ in range(token_guard.MAX_PER_CLIENT):
        assert anon_client.post("/oauth/token", json=bad).status_code == 401
    locked = anon_client.post("/oauth/token", json={**bad, "client_secret": creds["client_secret"]})
    assert locked.status_code == 429 and locked.headers["retry-after"] == str(token_guard.WINDOW_S)
    # Another client from the same IP is unaffected (per-IP limit is higher).
    other = service.create_agent({"agent_id": "lockout_other", "display_name": "x", "allowed_actions": ["a"],
                                  "blocked_actions": [], "spend_cap_daily": Decimal("1")})[2]
    ok = anon_client.post("/oauth/token", json={"grant_type": "client_credentials", "client_id": other["client_id"],
                                                 "client_secret": other["client_secret"]})
    assert ok.status_code == 200
