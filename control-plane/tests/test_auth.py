"""Browser authentication (OIDC BFF), sessions, CSRF, MFA, step-up, invites, sandbox signup."""

import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import login_as, make_user, new_client
from fake_idp import ISSUER
from veyrqun import service
from veyrqun.auth import directory, sessions
from veyrqun.auth.deps import cookie_names


def _login(c, idp, *, sub=None, email=None, mfa=True, next_path="/", verified=True, step_up=False):
    r = c.get("/auth/login", params={"next": next_path, "step_up": step_up}, follow_redirects=False)
    assert r.status_code == 302
    code, state = idp.authenticate(r.headers["location"], sub=sub or f"sub-{uuid.uuid4()}",
                                   email=email or f"{uuid.uuid4().hex[:6]}@bank.test", mfa=mfa, email_verified=verified)
    return c.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)


# ---------------------------------------------------------------- the authorization request


def test_login_redirect_uses_pkce_state_and_nonce(anon_client, idp):
    r = anon_client.get("/auth/login", follow_redirects=False)
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43
    assert q["response_type"] == "code" and q["client_id"] == "veyrqun-dashboard"
    assert len(q["state"]) >= 24 and len(q["nonce"]) >= 24 and "openid" in q["scope"]


def test_step_up_forces_reauthentication(anon_client, idp):
    r = anon_client.get("/auth/login", params={"step_up": True}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["prompt"] == ["login"] and q["max_age"] == ["0"]


# ---------------------------------------------------------------- the callback


def test_successful_login_sets_hardened_cookies_and_never_exposes_tokens(anon_client, idp):
    r = _login(anon_client, idp, next_path="/fleet")
    assert r.status_code == 302 and r.headers["location"] == "https://app.test/fleet"
    raw = ";".join(r.headers.get_list("set-cookie"))
    session_cookie, csrf_cookie = cookie_names()
    assert session_cookie == "__Host-veyrqun_session"
    session_line = next(h for h in r.headers.get_list("set-cookie") if h.startswith(session_cookie))
    assert "HttpOnly" in session_line and "Secure" in session_line and "SameSite=lax" in session_line and "Path=/" in session_line
    assert "Domain" not in session_line
    assert "id_token" not in raw and "access_token" not in raw and "eyJ" not in raw


def test_state_is_single_use(anon_client, idp):
    r = anon_client.get("/auth/login", follow_redirects=False)
    code, state = idp.authenticate(r.headers["location"], sub="s1", email="a@bank.test")
    assert "auth_error" not in anon_client.get("/auth/callback", params={"code": code, "state": state},
                                               follow_redirects=False).headers["location"]
    replay = anon_client.get("/auth/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert "auth_error=invalid_login" in replay.headers["location"]


@pytest.mark.parametrize("override, header", [
    ({"nonce": "attacker-nonce"}, {}),
    ({"iss": "https://evil.test"}, {}),
    ({"aud": "some-other-client"}, {}),
    ({"exp": int(time.time()) - 600, "iat": int(time.time()) - 900}, {}),
    ({}, {"alg": "none"}),
    ({}, {"alg": "HS256"}),
    ({}, {"kid": "unknown-kid"}),
])
def test_bad_id_tokens_are_rejected(anon_client, idp, override, header):
    idp.overrides, idp.header_overrides = override, header
    r = _login(anon_client, idp)
    assert "auth_error=invalid_login" in r.headers["location"]
    assert not any(h.startswith(cookie_names()[0]) for h in r.headers.get_list("set-cookie"))


def test_unverified_email_is_refused(anon_client, idp):
    assert "email_not_verified" in _login(anon_client, idp, verified=False).headers["location"]


@pytest.mark.parametrize("evil", ["//evil.test/x", "https://evil.test", "/\\evil.test", "javascript:alert(1)"])
def test_no_open_redirect(anon_client, idp, evil):
    assert _login(anon_client, idp, next_path=evil).headers["location"] == "https://app.test/"


def test_pkce_is_enforced_by_the_token_exchange(anon_client, idp):
    r = anon_client.get("/auth/login", follow_redirects=False)
    code, state = idp.authenticate(r.headers["location"], sub="s2", email="b@bank.test")
    idp.codes[code]["code_challenge"] = "tampered"
    assert "invalid_login" in anon_client.get("/auth/callback", params={"code": code, "state": state},
                                              follow_redirects=False).headers["location"]


# ---------------------------------------------------------------- sessions


def test_me_reports_roles_permissions_and_mfa(anon_client, idp):
    user = make_user(("viewer",), email="viewer@bank.test")
    login_as(anon_client, user)
    me = anon_client.get("/auth/me").json()
    assert me["email"] == "viewer@bank.test" and me["roles"] == ["viewer"]
    assert "agents:read" in me["permissions"] and "fleet:stop" not in me["permissions"]


def test_csrf_and_origin_are_required_for_state_changes(anon_client):
    login_as(anon_client, make_user())
    token = anon_client.headers.pop("X-CSRF-Token")
    assert anon_client.post("/emergency-stop/travel_agent_01").status_code == 403
    anon_client.headers["X-CSRF-Token"] = "wrong"
    assert anon_client.post("/emergency-stop/travel_agent_01").status_code == 403
    anon_client.headers["X-CSRF-Token"] = token
    anon_client.headers["Origin"] = "https://evil.test"
    assert anon_client.post("/emergency-stop/travel_agent_01").status_code == 403
    anon_client.headers["Origin"] = "https://app.test"
    assert anon_client.post("/emergency-stop/travel_agent_01").status_code == 200


def test_privileged_roles_require_mfa(anon_client):
    login_as(anon_client, make_user(("security_operator",)), mfa=False)
    r = anon_client.get("/agents")
    assert r.status_code == 403 and "multi-factor" in r.json()["detail"]
    with new_client() as viewer:
        login_as(viewer, make_user(("viewer",)), mfa=False)
        assert viewer.get("/agents").status_code == 200  # viewers aren't forced to MFA


def test_step_up_needed_for_kill_switch(anon_client):
    login_as(anon_client, make_user(("security_operator",)), auth_time=int(time.time()) - 3600)
    r = anon_client.post("/emergency-stop")
    assert r.status_code == 401 and r.headers["x-veyrqun-step-up"].startswith("/auth/login?step_up=1")
    assert anon_client.post("/emergency-stop/travel_agent_01").status_code == 200  # single-agent stop: no step-up


def test_idle_timeout_and_absolute_lifetime(anon_client, monkeypatch):
    user = make_user(("viewer",))
    login_as(anon_client, user)
    assert anon_client.get("/auth/me").status_code == 200
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + sessions.DEFAULT_IDLE_S + 5)
    assert anon_client.get("/auth/me").status_code == 401


def test_admin_sessions_are_shorter_lived():
    admin = sessions.Session(user_id="u", issuer="i", sub="s", workspace_id="w", roles=["platform_admin"], mfa=True, auth_time=0)
    viewer = sessions.Session(user_id="u", issuer="i", sub="s", workspace_id="w", roles=["viewer"], mfa=True, auth_time=0)
    assert admin.limits() == (15 * 60, 8 * 3600) and viewer.limits() == (30 * 60, 12 * 3600)


def test_role_change_revokes_sessions_immediately(anon_client, ws):
    user = make_user(("viewer",))
    login_as(anon_client, user)
    directory.grant_role(ws, user, "app_owner", "test")
    assert anon_client.get("/auth/me").status_code == 401


def test_logout_destroys_the_session(anon_client):
    login_as(anon_client, make_user(("viewer",)))
    assert anon_client.post("/auth/logout").status_code == 200
    assert anon_client.get("/auth/me").status_code == 401


def test_backchannel_logout_revokes_by_idp_session(anon_client, idp):
    r = _login(anon_client, idp, sub="bc-user", email="bc@bank.test")
    for line in r.headers.get_list("set-cookie"):
        name, _, rest = line.partition("=")
        anon_client.cookies.set(name, rest.split(";")[0], domain="testserver.local")
    user = directory.upsert_user(ISSUER, "bc-user", "bc@bank.test", None)
    directory.grant_role(service.default_workspace(), user, "viewer", "test")  # revokes; log in again
    r = _login(anon_client, idp, sub="bc-user", email="bc@bank.test")
    for line in r.headers.get_list("set-cookie"):
        name, _, rest = line.partition("=")
        anon_client.cookies.set(name, rest.split(";")[0], domain="testserver.local")
    assert anon_client.get("/auth/me").status_code == 200
    assert anon_client.post("/auth/backchannel-logout",
                            data={"logout_token": idp.logout_token("bc-user", sid="sid-bc-user")}).status_code == 200
    assert anon_client.get("/auth/me").status_code == 401


def test_disabled_users_cannot_log_in(anon_client, idp, ws):
    user = directory.upsert_user(ISSUER, "dis-user", "dis@bank.test", None)
    directory.set_status(user, "disabled", "test", ws)
    assert "account_is_disabled" in _login(anon_client, idp, sub="dis-user", email="dis@bank.test").headers["location"]


# ---------------------------------------------------------------- invites


def test_invite_is_bound_to_email_and_single_use(anon_client, idp, ws):
    invite = directory.create_invite(ws, "new.hire@bank.test", "auditor", "test")
    r = anon_client.get(f"/auth/invite/{invite['token']}", follow_redirects=False)
    assert r.headers["location"].startswith("/auth/login")
    anon_client.cookies.set("veyrqun_invite", invite["token"], domain="testserver.local")
    wrong = _login(anon_client, idp, sub="intruder", email="intruder@evil.test")
    assert "different_email" in wrong.headers["location"]
    anon_client.cookies.set("veyrqun_invite", invite["token"], domain="testserver.local")
    ok = _login(anon_client, idp, sub="hire", email="new.hire@bank.test")
    assert "auth_error" not in ok.headers["location"]
    user = directory.upsert_user(ISSUER, "hire", "new.hire@bank.test", None)
    assert directory.roles_in(user, ws) == {"auditor"}
    assert directory.peek_invite(invite["token"]) is None  # used up


def test_expired_or_revoked_invites_are_refused(anon_client, ws):
    invite = directory.create_invite(ws, "x@bank.test", "viewer", "test")
    directory.revoke_invite(ws, uuid.UUID(invite["invite_id"]), "test")
    assert "invite_invalid" in anon_client.get(f"/auth/invite/{invite['token']}", follow_redirects=False).headers["location"]


# ---------------------------------------------------------------- sandbox self-signup


def test_self_signup_only_creates_an_isolated_expiring_sandbox(anon_client, idp):
    r = _login(anon_client, idp, sub="newcomer", email="newcomer@startup.test")
    for line in r.headers.get_list("set-cookie"):
        name, _, rest = line.partition("=")
        anon_client.cookies.set(name, rest.split(";")[0], domain="testserver.local")
    csrf = anon_client.cookies.get(cookie_names()[1], domain="testserver.local")
    anon_client.headers.update({"X-CSRF-Token": csrf, "Origin": "https://app.test"})
    me = anon_client.get("/auth/me").json()
    assert me["workspace_id"] is None and me["roles"] == []
    assert anon_client.get("/agents").status_code == 403  # no access to anything real
    old_session = anon_client.cookies.get(cookie_names()[0], domain="testserver.local")
    resp = anon_client.post("/auth/sandbox", json={})
    box = resp.json()
    assert box["slug"].startswith("sbx-") and set(box["roles"]) == {"security_operator", "app_owner"}
    # Privileges changed, so the session was rotated: new id and CSRF token, and the old id no longer works.
    new_session = resp.cookies.get(cookie_names()[0])
    assert new_session and new_session != old_session
    assert sessions.load(old_session) is None
    anon_client.cookies.set(cookie_names()[0], new_session, domain="testserver.local")
    anon_client.cookies.set(cookie_names()[1], resp.cookies.get(cookie_names()[1]), domain="testserver.local")
    anon_client.headers["X-CSRF-Token"] = resp.cookies.get(cookie_names()[1])
    assert anon_client.post("/auth/sandbox", json={}).status_code == 409  # one active sandbox per person
    assert anon_client.get("/agents").json() == []  # nothing from the default workspace leaks in


def test_expired_sandboxes_are_purged(owner_engine):
    from sqlalchemy import text

    user = directory.upsert_user(ISSUER, "sbx", "sbx@x.test", None)
    sbx = directory.create_sandbox(user, "sbx@x.test")
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE iam.workspaces SET expires_at = now() - interval '1 minute' WHERE id = :w"), {"w": sbx})
    assert directory.purge_expired_sandboxes() == 1


def test_admin_bootstrap_only_once(ws):
    directory.bootstrap_admin(ISSUER, "root", "root@bank.test")
    with pytest.raises(directory.DirectoryError, match="already exists"):
        directory.bootstrap_admin(ISSUER, "root2", "root2@bank.test")
