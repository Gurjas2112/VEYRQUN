"""Tenant isolation: Postgres row-level security plus IDOR (id-swap) tests across workspaces."""

from decimal import Decimal

import pytest
from sqlalchemy import text

from conftest import ISSUER, login_as, make_user, new_client
from veyrqun import service
from veyrqun.auth import directory
from veyrqun.db import PLATFORM, tx


@pytest.fixture
def two_tenants():
    """Workspace A = default (seeded fleet); workspace B = a sandbox with its own agent, decision, approval."""
    owner_b = directory.upsert_user(ISSUER, "owner-b", "b@other.test", None)
    ws_b = directory.create_sandbox(owner_b, "b@other.test")
    service.create_agent({"agent_id": "b_agent", "display_name": "B", "allowed_actions": ["pay"], "blocked_actions": [],
                          "spend_cap_daily": Decimal("1000"), "approval_threshold": Decimal("500")}, workspace_id=ws_b)
    entry, _, details = service.attempt_action("b_agent", "pay", Decimal("600"), workspace_id=ws_b)
    return {"ws_b": ws_b, "entry_b": entry, "approval_b": details["approval_id"]}


def test_rls_hides_other_tenants_rows_at_the_database(two_tenants, ws):
    for table in ("gov.agents", "gov.approvals", "gov.fleet_state", "audit.events", "iam.memberships"):
        with tx(ws) as conn:
            rows = conn.execute(text(f"SELECT count(*) FROM {table} WHERE workspace_id = :b"),  # noqa: S608
                                {"b": two_tenants["ws_b"]}).scalar_one()
        assert rows == 0, f"{table} leaked rows from another workspace"
    with tx(ws) as conn:  # child tables are isolated through their agent
        assert conn.execute(text("SELECT count(*) FROM gov.policy_versions p JOIN gov.agents a ON a.id = p.agent_id "
                                 "WHERE a.agent_key = 'b_agent'")).scalar_one() == 0


def test_no_scope_means_no_rows_fail_closed(two_tenants):
    with tx() as conn:
        assert conn.execute(text("SELECT count(*) FROM gov.agents")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM audit.events")).scalar_one() == 0


def test_rls_blocks_cross_tenant_writes(two_tenants, ws):
    with pytest.raises(Exception), tx(ws) as conn:  # noqa: B017
        conn.execute(text("INSERT INTO gov.fleet_state (workspace_id) VALUES (:b) ON CONFLICT (workspace_id) "
                          "DO UPDATE SET stopped = true"), {"b": two_tenants["ws_b"]})
    with tx(PLATFORM) as conn:
        assert conn.execute(text("SELECT stopped FROM gov.fleet_state WHERE workspace_id = :b"),
                            {"b": two_tenants["ws_b"]}).scalar_one() is False


@pytest.mark.parametrize("method, path", [
    ("GET", "/agents/b_agent"),
    ("GET", "/agents/b_agent/policy-history"),
    ("PATCH", "/agents/b_agent/policy"),
    ("POST", "/agents/b_agent/revoke"),
    ("POST", "/emergency-stop/b_agent"),
    ("POST", "/agents/b_agent/rotate-secret"),
])
def test_idor_agent_ids_from_other_tenants_are_not_found(two_tenants, method, path):
    with new_client() as client:
        login_as(client, make_user(("security_operator", "app_owner", "approver", "viewer")))
        r = client.request(method, path, json={"note": "x"} if method == "PATCH" else None)
    assert r.status_code == 404


def test_idor_audit_entries_and_approvals_from_other_tenants(two_tenants):
    with new_client() as client:
        login_as(client, make_user(("approver", "viewer")))
        assert client.get(f"/audit/{two_tenants['entry_b']['id']}").status_code == 404
        assert client.post(f"/approvals/{two_tenants['approval_b']}/approve").status_code == 404
        assert all(a["approval_id"] != two_tenants["approval_b"] for a in client.get("/approvals").json())
        assert all(e["agent_id"] != "b_agent" for e in client.get("/audit-log", params={"limit": 200}).json())


def test_cannot_switch_into_a_workspace_you_are_not_a_member_of(two_tenants):
    with new_client() as client:
        login_as(client, make_user(("viewer",)))
        assert client.post("/auth/switch", json={"workspace_id": str(two_tenants["ws_b"])}).status_code == 403


def test_agent_tokens_are_bound_to_their_own_agent_and_workspace(two_tenants):
    with new_client() as client:
        login_as(client, make_user())
        creds = client.post("/agents", json={"agent_id": "a_agent", "display_name": "A", "allowed_actions": ["pay"],
                                             "spend_cap_daily": "100"}).json()["credentials"]
    with new_client() as agent:
        assert agent.post("/oauth/token", json={"grant_type": "client_credentials", **creds}).status_code == 400
        req = {"grant_type": "client_credentials", "client_id": creds["client_id"], "client_secret": creds["client_secret"]}
        token = agent.post("/oauth/token", json=req).json()["access_token"]
        agent.headers["Authorization"] = f"Bearer {token}"
        own = agent.post("/simulate/action", json={"agent_id": "a_agent", "action_type": "pay", "amount": "1"})
        assert own.status_code == 200 and own.json()["outcome"] == "allow"
        other = agent.post("/simulate/action", json={"agent_id": "travel_agent_01", "action_type": "flight_rebook",
                                                     "amount": "1"})
        assert other.status_code == 403
        assert agent.post("/simulate/action", json={"agent_id": "b_agent", "action_type": "pay", "amount": "1"}).status_code == 403
        assert agent.get("/agents").status_code == 403  # agent tokens are not operator credentials


def test_live_feed_only_streams_the_sessions_workspace(two_tenants):
    with new_client() as client:
        login_as(client, make_user(("viewer",)))
        with client.websocket_connect("/ws/live-feed") as feed:
            import asyncio

            from veyrqun.ws import ws_manager

            entry_b, _, _ = service.attempt_action("b_agent", "pay", Decimal("1"), workspace_id=two_tenants["ws_b"])
            entry_a, _, _ = service.attempt_action("travel_agent_01", "flight_rebook", Decimal("1"))
            loop = asyncio.new_event_loop()
            loop.run_until_complete(ws_manager.broadcast(entry_b))
            loop.run_until_complete(ws_manager.broadcast(entry_a))
            assert feed.receive_json()["agent_id"] == "travel_agent_01"
