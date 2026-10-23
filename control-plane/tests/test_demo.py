"""Public demo: anonymous guests, their sandbox, the MFA waiver's limits, and the hard expiry."""

from datetime import UTC, datetime, timedelta

import pytest

from conftest import login_as, make_user, new_client
from veyrqun import service
from veyrqun.auth import sessions
from veyrqun.auth.deps import cookie_names
from veyrqun.config import get_settings


@pytest.fixture
def demo_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "demo_mode", True)
    monkeypatch.setattr(s, "demo_expires_at", datetime.now(UTC) + timedelta(days=5))
    return s


def _guest(client):
    r = client.post("/auth/demo", headers={"Origin": "https://app.test"})
    assert r.status_code == 201, r.text
    session_cookie, csrf_cookie = cookie_names()
    client.headers.update({"X-CSRF-Token": client.cookies.get(csrf_cookie), "Origin": "https://app.test"})
    return r.json(), client.cookies.get(session_cookie)


def test_demo_is_off_unless_enabled():
    with new_client() as c:
        assert c.get("/auth/demo").json() == {"enabled": False, "active": False, "expires_at": None, "sso": True}
        assert c.post("/auth/demo").status_code == 404


def test_guest_gets_a_seeded_private_sandbox(demo_on):
    with new_client() as c:
        body, _ = _guest(c)
        me = c.get("/auth/me").json()
        assert me["demo"]["guest"] and me["demo"]["active"] and me["workspace_id"] == body["workspace_id"]
        assert set(me["roles"]) == {"security_operator", "app_owner", "approver"} and me["mfa"] is False
        agents = {a["agent_id"] for a in c.get("/agents").json()}
        assert agents == {"fee_reversal_bot", "card_replacement_bot", "travel_bot", "rogue_bot"}
        assert service.workspace_env(service.default_workspace()) != "sandbox"
        assert body["workspace_id"] != str(service.default_workspace())


def test_guest_can_operate_without_mfa_inside_the_sandbox_only(demo_on):
    with new_client() as c:
        body, session_id = _guest(c)
        assert c.post("/emergency-stop/rogue_bot").status_code == 200  # security_operator action, no MFA, no step-up
        assert c.post("/emergency-stop").status_code == 200            # fleet:stop is a step-up permission
        # Point the guest session at a real workspace: the waiver must not follow it there.
        s = sessions.load(session_id)
        s.workspace_id = str(service.default_workspace())
        sessions.update(session_id, s)
        r = c.get("/agents")
        assert r.status_code == 403 and "sandbox" in r.json()["detail"]


def test_the_waiver_is_only_for_guest_sessions(demo_on):
    with new_client() as c:
        login_as(c, make_user(("security_operator",)), mfa=False)
        assert c.post("/emergency-stop/travel_agent_01").status_code == 403  # normal users still need MFA


def test_guests_are_isolated_from_each_other(demo_on):
    with new_client() as a, new_client() as b:
        ws_a, _ = _guest(a)
        ws_b, _ = _guest(b)
        assert ws_a["workspace_id"] != ws_b["workspace_id"]
        a.post("/emergency-stop/rogue_bot")
        status_b = {x["agent_id"]: x["status"] for x in b.get("/agents").json()}
        assert status_b["rogue_bot"] == "active"


def test_rate_limit_per_ip(demo_on, monkeypatch):
    monkeypatch.setattr(demo_on, "demo_guests_per_ip_per_hour", 2)
    with new_client() as c:
        codes = [c.post("/auth/demo", headers={"Origin": "https://app.test"}).status_code for _ in range(3)]
    assert codes == [201, 201, 429]


def test_cross_site_guest_login_is_refused(demo_on):
    with new_client() as c:
        assert c.post("/auth/demo", headers={"Origin": "https://evil.example"}).status_code == 403


def test_everything_stops_at_expiry(demo_on, monkeypatch):
    with new_client() as c:
        body, _ = _guest(c)
        monkeypatch.setattr(demo_on, "demo_expires_at", datetime.now(UTC) - timedelta(seconds=1))
        assert c.get("/auth/demo").json()["active"] is False
        assert c.get("/agents").status_code == 410                    # existing guest sessions end
    with new_client() as c:
        assert c.post("/auth/demo", headers={"Origin": "https://app.test"}).status_code == 410
        # Sandbox bundles are refused, so gateways fail closed for them; real workspaces are unaffected.
        assert c.get(f"/bundles/current?workspace_id={body['workspace_id']}").status_code == 410
        assert c.get("/bundles/current?workspace=default").status_code == 200


def test_stats_are_scoped_to_the_callers_workspace(demo_on):
    from decimal import Decimal

    service.attempt_action("travel_agent_01", "flight_rebook", Decimal("10"))  # default workspace activity
    with new_client() as c:
        _guest(c)
        stats = c.get("/stats").json()
        assert stats["decisions"] == 0 and "travel_agent_01" not in stats["per_agent"]
