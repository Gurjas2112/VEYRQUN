"""Data protection, maker-checker in production, integrity jobs, signed export, HTTP hardening."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from conftest import ISSUER, login_as, make_user, new_client
from veyrqun import audit, counters, jobs, service
from veyrqun.auth import directory
from veyrqun.db import PLATFORM, tx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_export  # noqa: E402

# ---------------------------------------------------------------- personal data


def test_emails_are_encrypted_at_rest_and_looked_up_by_keyed_hash(owner_engine):
    user = directory.upsert_user(ISSUER, "pii-user", "Jane.Doe@Bank.test", "Jane Doe")
    with owner_engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM iam.users WHERE id = :u"), {"u": user}).first()
    assert b"jane" not in bytes(row.email_enc).lower() and b"Jane" not in bytes(row.display_enc)
    assert row.email_hash == directory.email_hash("jane.doe@bank.test")  # case-insensitive, deterministic
    assert directory.identity_of(user)["email"] == "jane.doe@bank.test"


def test_crypto_shredding_erases_identity_but_keeps_the_audit_chain_valid(ws):
    user = directory.upsert_user(ISSUER, "leaver-sub", "leaver@bank.test", "Leaver")
    directory.grant_role(ws, user, "viewer", "test")
    service.attempt_action("travel_agent_01", "flight_rebook", Decimal("5"), actor=f"user:{user}")
    directory.erase(user, "test:admin", ws)
    assert directory.identity_of(user) == {"user_id": str(user), "email": None, "name": None, "status": "erased"}
    assert audit.verify_chain()["valid"] is True
    assert any(e["actor"] == f"user:{user}" for e in audit.query(ws, limit=50))  # pseudonymous id remains
    with pytest.raises(directory.DirectoryError, match="erased"):  # the same IdP subject can't log back in
        directory.upsert_user(ISSUER, "leaver-sub", "leaver@bank.test", None)
    with tx(PLATFORM) as conn:
        assert conn.execute(text("SELECT key_enc FROM iam.identity_keys WHERE user_id = :u"), {"u": user}).scalar() is None


def test_separation_of_duties_blocks_auditor_plus_operator(ws):
    user = make_user(("auditor",))
    with pytest.raises(directory.DirectoryError, match="separation of duties"):
        directory.grant_role(ws, user, "security_operator", "test")


# ---------------------------------------------------------------- maker-checker (production)


@pytest.fixture
def production(owner_engine, ws):
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE iam.workspaces SET environment = 'production' WHERE id = :w"), {"w": ws})


def test_policy_publish_in_production_needs_a_second_person(production):
    with new_client() as maker, new_client() as checker:
        login_as(maker, make_user(("security_operator",)))
        login_as(checker, make_user(("approver",)))
        r = maker.patch("/agents/travel_agent_01/policy", json={"spend_cap_daily": "1600", "note": "Q4 travel"})
        assert r.status_code == 202
        approval = r.json()["approval"]
        assert maker.get("/agents/travel_agent_01").json()["policy_version"] == 1  # nothing applied yet
        assert checker.post(f"/approvals/{approval['approval_id']}/approve").json()["status"] == "consumed"
        agent = maker.get("/agents/travel_agent_01").json()
        assert agent["policy_version"] == 2 and agent["spend_cap_daily"] == "1600.00"
        history = maker.get("/agents/travel_agent_01/policy-history").json()
        assert history[0]["created_by"] == approval["requested_by"] and "approved by user:" in history[0]["note"]


def test_maker_cannot_approve_their_own_change(production):
    user = make_user(("security_operator", "approver"))
    with new_client() as c:
        login_as(c, user)
        approval = c.patch("/agents/travel_agent_01/policy", json={"spend_cap_daily": "1600"}).json()["approval"]
        r = c.post(f"/approvals/{approval['approval_id']}/approve")
        assert r.status_code == 403 and "maker-checker" in r.json()["detail"]


def test_stale_change_request_is_refused(production):
    with new_client() as maker, new_client() as checker:
        login_as(maker, make_user(("security_operator",)))
        login_as(checker, make_user(("approver",)))
        first = maker.patch("/agents/travel_agent_01/policy", json={"spend_cap_daily": "1600"}).json()["approval"]
        second = maker.patch("/agents/travel_agent_01/policy", json={"spend_cap_daily": "1700"}).json()["approval"]
        assert checker.post(f"/approvals/{first['approval_id']}/approve").status_code == 200
        r = checker.post(f"/approvals/{second['approval_id']}/approve")
        assert r.status_code == 409 and "changed since the request" in r.json()["detail"]


def test_kill_switch_release_needs_a_second_person_but_activation_does_not(production):
    with new_client() as op, new_client() as checker:
        login_as(op, make_user(("security_operator",)))
        login_as(checker, make_user(("approver",)))
        assert op.post("/emergency-stop").status_code == 200
        release = op.post("/resume-all")
        assert release.status_code == 202
        assert op.get("/fleet/status").json()["fleet_stopped"] is True
        checker.post(f"/approvals/{release.json()['approval']['approval_id']}/approve")
        assert op.get("/fleet/status").json()["fleet_stopped"] is False


def test_app_owner_submissions_are_always_change_requests(ws):
    with new_client() as owner:
        login_as(owner, make_user(("app_owner",)))
        r = owner.patch("/agents/travel_agent_01/policy", json={"spend_cap_daily": "100"})
        assert r.status_code == 202  # even in a staging workspace


def test_granting_platform_admin_needs_a_second_person(ws):
    target = make_user(("viewer",))
    with new_client() as admin:
        login_as(admin, make_user(("platform_admin",)))
        r = admin.post(f"/admin/members/{target}/roles", json={"role": "platform_admin"})
        assert r.status_code == 202
        assert "platform_admin" not in directory.roles_in(target, ws)


# ---------------------------------------------------------------- integrity jobs


def test_reconciliation_finds_no_drift_then_detects_tampering(ws):
    service.update_policy("travel_agent_01", {"rate_limit_per_minute": None})
    for amount in ("100", "250.50", "9.99"):
        service.attempt_action("travel_agent_01", "flight_rebook", Decimal(amount))
    service.attempt_action("travel_agent_01", "credit_limit_increase", Decimal("50"))  # blocked: not counted
    clean = jobs.reconcile_spend(ws)
    assert clean["ok"] and clean["agents_checked"] == 5 and clean["signature"]
    key = counters.keys(str(ws), "travel_agent_01", "_", "UTC")[0]
    counters.client().incrby(key, 5_000)  # someone bumps the live counter outside the policy path
    drift = jobs.reconcile_spend(ws)
    assert not drift["ok"] and drift["drifts"][0]["agent_id"] == "travel_agent_01"
    entry = audit.get(drift["audit_entry_id"], ws)
    assert entry["action_type"] == "integrity:reconcile_spend" and entry["rule_id"] == "reconcile_spend_failed"


def test_reset_is_respected_by_reconciliation(ws):
    service.attempt_action("travel_agent_01", "flight_rebook", Decimal("100"))
    service.reset_spend("travel_agent_01")
    service.attempt_action("travel_agent_01", "flight_rebook", Decimal("40"))
    assert jobs.reconcile_spend(ws)["ok"]


def test_counters_can_be_rebuilt_from_the_ledger_after_redis_loss(ws):
    service.attempt_action("travel_agent_01", "flight_rebook", Decimal("300"))
    counters.client().flushdb()
    assert jobs.rebuild_counters(ws) == 5
    assert service.get_agent("travel_agent_01")["spend_used_today"] == "300.00"


def test_chain_check_job_is_signed_and_recorded(ws):
    service.attempt_action("travel_agent_01", "flight_rebook", Decimal("1"))
    result = jobs.chain_check(ws)
    assert result["ok"] and result["valid"] and len(result["signature"]) > 40


# ---------------------------------------------------------------- signed export


def test_signed_export_verifies_offline_and_detects_tampering(ws):
    for i in range(5):
        service.attempt_action("travel_agent_01", "flight_rebook", Decimal(str(10 + i)))
    with new_client() as auditor:
        login_as(auditor, make_user(("auditor",)))
        raw = auditor.get("/audit/export").content
        jwks = auditor.get("/.well-known/jwks.json").json()
    ok = verify_export.verify(raw, jwks)
    # 5 decisions plus the role-grant audit entries written when the test users were set up.
    assert ok["valid"] and ok["entries"] >= 5 and ok["links_checked"] == ok["entries"] - 1
    tampered = raw.replace(b'"amount":"12.00"', b'"amount":"1.00"', 1)
    bad = verify_export.verify(tampered, jwks)
    assert not bad["valid"] and any("body" in p or "hash" in p for p in bad["problems"])


def test_only_auditors_export(ws):
    with new_client() as op:
        login_as(op, make_user(("security_operator",)))
        assert op.get("/audit/export").status_code == 403


# ---------------------------------------------------------------- HTTP hardening


def test_security_headers_present(anon_client):
    h = anon_client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" in h  # https base_url


def test_oversized_bodies_are_rejected(client):
    r = client.post("/simulate/action", content=b"{" + b" " * 1_100_000 + b"}",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
