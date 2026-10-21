"""End-to-end identity flows through a real browser and a real Keycloak (password + TOTP).

Prereqs: `docker compose up -d postgres redis keycloak` and
         `python deploy/keycloak/seed_dev_users.py --out .dev-users.json`
Run:     python -m pytest e2e -q

The control plane runs as a subprocess on :8000 against a fresh database `veyrqun_e2e`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pyotp
import pytest
from playwright.sync_api import Page, sync_playwright
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
CP = "http://localhost:8000"
ISSUER = "http://localhost:8180/realms/veyrqun"
OWNER = "postgresql+psycopg://veyrqun_owner:veyrqun_owner_dev@localhost:55432"
USERS = json.loads((ROOT / ".dev-users.json").read_text()) if (ROOT / ".dev-users.json").exists() else {}

pytestmark = pytest.mark.skipif(not USERS, reason="run deploy/keycloak/seed_dev_users.py first")


@pytest.fixture(scope="session")
def control_plane():
    admin = create_engine(f"{OWNER}/postgres", isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS veyrqun_e2e WITH (FORCE)"))
        c.execute(text("CREATE DATABASE veyrqun_e2e"))
        c.execute(text("GRANT CONNECT ON DATABASE veyrqun_e2e TO veyrqun_app"))
    env = {**os.environ,
           "VEYRQUN_ENV": "dev", "VEYRQUN_SIMULATOR": "0",
           "DATABASE_URL": "postgresql+psycopg://veyrqun_app:veyrqun_app_dev@localhost:55432/veyrqun_e2e",
           "MIGRATION_DATABASE_URL": f"{OWNER}/veyrqun_e2e",
           "REDIS_URL": "redis://localhost:56379/13",
           "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_SECRET": os.environ.get("KC_DASHBOARD_CLIENT_SECRET", "dev-dashboard-secret"),
           "OIDC_REDIRECT_URI": f"{CP}/auth/callback", "APP_BASE_URL": CP, "ALLOWED_ORIGINS": CP,
           "DEFAULT_WORKSPACE_ENV": "production"}
    cpdir = ROOT / "control-plane"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=cpdir, env=env, check=True,
                   capture_output=True)
    subprocess.run([sys.executable, "-m", "veyrqun.cli", "admin", "bootstrap", "--issuer", ISSUER,
                    "--sub", USERS["admin"]["sub"], "--email", USERS["admin"]["email"]], cwd=cpdir, env=env, check=True)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", "8000", "--log-level", "info"], cwd=cpdir, env=env,
                            stdout=open(ROOT / "e2e" / "control-plane.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if httpx.get(f"{CP}/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    yield env
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, control_plane):
    ctx = browser.new_context()
    yield ctx.new_page()
    ctx.close()


_last_window: dict[str, int] = {}


def fresh_totp(username: str) -> str:
    """Keycloak rejects a TOTP code that was already used (replay protection), so never reuse a window."""
    window = int(time.time()) // 30
    if _last_window.get(username) == window:
        time.sleep(30 - time.time() % 30 + 0.5)
        window = int(time.time()) // 30
    _last_window[username] = window
    return pyotp.TOTP(USERS[username]["totp_base32"]).now()


def keycloak_login(page: Page, username: str, start: str = "/auth/login?next=/auth/me") -> dict:
    u = USERS[username]
    page.goto(f"{CP}{start}")
    page.fill("#username", u["email"])
    page.fill("#password", u["password"])
    page.click("#kc-login")
    if u["totp_base32"]:
        page.wait_for_selector("#otp")
        page.fill("#otp", fresh_totp(username))
        page.click("#kc-login")
    page.wait_for_url(f"{CP}/auth/me*")
    return json.loads(page.inner_text("body"))


def api(page: Page, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Call the API with the browser context's own cookies, plus the CSRF header and Origin the dashboard
    sends. (In-page fetch from an API response is blocked by the API's CSP, `default-src 'none'`, by design.)"""
    csrf = next((c["value"] for c in page.context.cookies() if c["name"] == "__Host-veyrqun_csrf"), "")
    r = page.context.request.fetch(f"{CP}{path}", method=method, data=json.dumps(body) if body is not None else None,
                                   headers={"Content-Type": "application/json", "X-CSRF-Token": csrf, "Origin": CP})
    try:
        return r.status, r.json()
    except Exception:
        return r.status, {}


def test_admin_logs_in_with_password_and_totp(page):
    me = keycloak_login(page, "admin")
    assert me["roles"] == ["platform_admin"] and me["mfa"] is True
    cookies = {c["name"]: c for c in page.context.cookies()}
    session = cookies["__Host-veyrqun_session"]
    assert session["httpOnly"] and session["secure"] and session["sameSite"] == "Lax"
    ours = [c for c in page.context.cookies() if c["name"].startswith(("__Host-veyrqun", "veyrqun_"))]
    assert ours and all("eyJ" not in c["value"] for c in ours)  # no IdP tokens ever reach the browser
    # Keycloak's own SSO cookies live under its realm path, so they're never sent to the API.
    assert all(c["path"].startswith("/realms/") for c in page.context.cookies() if c["name"].startswith("KEYCLOAK"))


def test_invite_flow_and_email_binding(browser, control_plane):
    admin = browser.new_context().new_page()
    keycloak_login(admin, "admin")
    status, invite = api(admin, "POST", "/admin/invites", {"email": USERS["approver"]["email"], "role": "approver"})
    assert status == 201
    status, wrong = api(admin, "POST", "/admin/invites", {"email": "someone.else@veyrqun.dev", "role": "viewer"})

    invitee = browser.new_context().new_page()
    invitee.goto(invite["accept_url"])           # sets the invite cookie, then goes to login
    invitee.fill("#username", USERS["approver"]["email"])
    invitee.fill("#password", USERS["approver"]["password"])
    invitee.click("#kc-login")
    invitee.fill("#otp", fresh_totp("approver"))
    invitee.click("#kc-login")
    invitee.wait_for_load_state()
    invitee.goto(f"{CP}/auth/me")
    me = json.loads(invitee.inner_text("body"))
    assert me["roles"] == ["approver"] and me["mfa"] is True

    other = browser.new_context().new_page()     # the viewer tries someone else's invite link
    other.goto(wrong["accept_url"])
    other.fill("#username", USERS["viewer"]["email"])
    other.fill("#password", USERS["viewer"]["password"])
    other.click("#kc-login")
    other.wait_for_url("**auth_error=invite_was_issued_to_a_different_email_address*")


def test_privileged_role_without_mfa_is_refused(browser, control_plane):
    admin = browser.new_context().new_page()
    keycloak_login(admin, "admin")
    _, invite = api(admin, "POST", "/admin/invites", {"email": USERS["nomfa.operator"]["email"], "role": "security_operator"})
    page = browser.new_context().new_page()
    page.goto(invite["accept_url"])
    page.fill("#username", USERS["nomfa.operator"]["email"])
    page.fill("#password", USERS["nomfa.operator"]["password"])
    page.click("#kc-login")
    page.wait_for_load_state()
    page.goto(f"{CP}/auth/me")
    me = json.loads(page.inner_text("body"))
    assert me["roles"] == ["security_operator"] and me["mfa"] is False
    status, body = api(page, "GET", "/agents")
    assert status == 403 and "multi-factor" in body["detail"]


def test_newcomer_can_only_create_a_sandbox(page):
    me = keycloak_login(page, "newbie")
    assert me["workspace_id"] is None and me["roles"] == []
    assert api(page, "GET", "/agents")[0] == 403
    status, box = api(page, "POST", "/auth/sandbox", {})
    assert status == 201 and box["slug"].startswith("sbx-")
    status, agents = api(page, "GET", "/agents")
    assert status == 200 and agents == []


def test_logout_ends_the_session(page):
    keycloak_login(page, "viewer")
    assert api(page, "POST", "/auth/logout")[0] == 200
    assert api(page, "GET", "/auth/me")[0] == 401
