"""Authorization tests generated from the role matrix (veyrqun/auth/roles.py).

Every endpoint in the OpenAPI schema must be classified below; an unclassified endpoint fails the
build, so new routes can't ship without an authorization decision. Then every endpoint is called as
every role (and anonymously) and the status is checked against PERMISSIONS: 401 for anonymous,
403 for roles without the permission, and anything *but* 401/403 for roles with it.
"""

import uuid

import pytest

from conftest import login_as, make_user, new_client
from veyrqun import service
from veyrqun.auth.roles import PERMISSIONS, ROLES
from veyrqun.main import app


@pytest.fixture(autouse=True)
def _idp(idp):
    """/auth/login builds a real authorization URL against the in-process IdP."""
    yield idp

PUBLIC = "public"
SERVICE = "service"   # gateway service token only
SESSION = "session"   # any authenticated user, no specific permission

ENDPOINTS: dict[tuple[str, str], str] = {
    ("GET", "/auth/login"): PUBLIC,
    ("GET", "/auth/callback"): PUBLIC,
    ("POST", "/auth/logout"): SESSION,
    ("POST", "/auth/backchannel-logout"): PUBLIC,
    ("GET", "/auth/me"): SESSION,
    ("GET", "/auth/me/data"): SESSION,
    ("POST", "/auth/switch"): SESSION,
    ("GET", "/auth/invite/{token}"): PUBLIC,
    ("GET", "/auth/demo"): PUBLIC,
    ("POST", "/auth/demo"): PUBLIC,
    ("POST", "/auth/sandbox"): SESSION,
    ("GET", "/agents"): "agents:read",
    ("POST", "/agents"): "agents:register",
    ("GET", "/agents/{agent_id}"): "agents:read",
    ("POST", "/agents/{agent_id}/rotate-secret"): "agents:rotate_secret",
    ("PATCH", "/agents/{agent_id}/policy"): "policy:edit",
    ("GET", "/agents/{agent_id}/policy-history"): "policy:read",
    ("POST", "/agents/{agent_id}/policy/rollback/{version}"): "policy:edit",
    ("POST", "/agents/{agent_id}/reset-spend"): "spend:reset",
    ("POST", "/reset-all-spend"): "spend:reset",
    ("POST", "/simulate/action"): "actions:simulate",
    ("GET", "/simulator"): "metrics:read",
    ("POST", "/simulator/toggle"): "simulator:toggle",
    ("GET", "/audit-log"): "audit:read",
    ("GET", "/audit-log/verify"): "audit:verify",
    ("GET", "/audit/export"): "audit:export",
    ("GET", "/audit/{entry_id}"): "audit:read",
    ("GET", "/audit/{entry_id}/verify"): "audit:read",
    ("GET", "/audit/pipeline"): "audit:verify",
    ("GET", "/audit/anchors"): "audit:verify",
    ("GET", "/audit/decision/{decision_id}"): "audit:read",
    ("POST", "/playground/run"): "actions:simulate",
    ("GET", "/stats"): "metrics:read",
    ("GET", "/fleet/status"): "agents:read",
    ("POST", "/emergency-stop"): "fleet:stop",
    ("POST", "/emergency-stop/{agent_id}"): "agents:control",
    ("POST", "/resume-all"): "fleet:release",
    ("POST", "/resume/{agent_id}"): "agents:control",
    ("POST", "/agents/{agent_id}/revoke"): "agents:control",
    ("POST", "/agents/{agent_id}/reinstate"): "agents:control",
    ("GET", "/approvals"): "approvals:read",
    ("POST", "/approvals/{approval_id}/approve"): "approvals:decide",
    ("POST", "/approvals/{approval_id}/reject"): "approvals:decide",
    ("GET", "/admin/members"): "iam:read",
    ("POST", "/admin/invites"): "iam:manage",
    ("DELETE", "/admin/invites/{invite_id}"): "iam:manage",
    ("POST", "/admin/members/{user_id}/roles"): "iam:manage",
    ("DELETE", "/admin/members/{user_id}/roles/{role}"): "iam:manage",
    ("POST", "/admin/users/{user_id}/disable"): "iam:manage",
    ("POST", "/admin/users/{user_id}/erase"): "iam:manage",
    ("GET", "/admin/users/{user_id}/sessions"): "iam:read",
    ("DELETE", "/admin/users/{user_id}/sessions/{session_ref}"): "iam:manage",
    ("POST", "/admin/keys/rotate"): "keys:rotate",
    ("GET", "/.well-known/jwks.json"): PUBLIC,
    ("POST", "/oauth/token"): PUBLIC,
    ("GET", "/bundles/current"): PUBLIC,          # service-token protected when configured
    ("GET", "/metrics"): "metrics:read",
    ("GET", "/metrics/prometheus"): PUBLIC,       # bearer METRICS_TOKEN when configured
    ("GET", "/health"): PUBLIC,
    ("POST", "/internal/approvals"): SERVICE,
    ("GET", "/internal/approvals/{approval_id}"): SERVICE,
    ("POST", "/internal/approvals/{approval_id}/consume"): SERVICE,
}

BODIES = {
    ("POST", "/auth/switch"): "own-workspace",
    ("POST", "/auth/sandbox"): {},
    ("POST", "/agents"): {"agent_id": "matrix_agent", "display_name": "M", "spend_cap_daily": "1"},
    ("PATCH", "/agents/{agent_id}/policy"): {"note": "matrix"},
    ("POST", "/simulate/action"): {"agent_id": "travel_agent_01", "action_type": "flight_rebook", "amount": "1"},
    ("POST", "/admin/invites"): {"email": "m@bank.test", "role": "viewer"},
    ("POST", "/admin/members/{user_id}/roles"): {"role": "viewer"},
    ("POST", "/admin/keys/rotate"): {"purpose": "bundle"},
    ("POST", "/oauth/token"): {"grant_type": "client_credentials", "client_id": "default:x", "client_secret": "y"},
}


def _schema_endpoints() -> set[tuple[str, str]]:
    return {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}


def test_every_endpoint_is_classified():
    unclassified = _schema_endpoints() - set(ENDPOINTS)
    stale = set(ENDPOINTS) - _schema_endpoints()
    assert not unclassified, f"add these endpoints to the authorization matrix: {sorted(unclassified)}"
    assert not stale, f"matrix lists endpoints that no longer exist: {sorted(stale)}"
    assert {p for p in ENDPOINTS.values() if p not in (PUBLIC, SESSION, SERVICE)} <= set(PERMISSIONS)


def _call(client, method, path, target_user):
    url = (path.replace("{agent_id}", "travel_agent_01").replace("{version}", "1")
           .replace("{approval_id}", str(uuid.uuid4())).replace("{entry_id}", str(uuid.uuid4()))
           .replace("{invite_id}", str(uuid.uuid4())).replace("{user_id}", str(target_user))
           .replace("{role}", "viewer").replace("{session_ref}", "deadbeef").replace("{token}", "vqi_x"))
    if path == "/simulator/toggle":
        url += "?running=true"
    body = BODIES.get((method, path))
    if body == "own-workspace":
        body = {"workspace_id": str(service.default_workspace())}
    return client.request(method, url, json=body, follow_redirects=False)


CASES = [(m, p, role) for (m, p), perm in sorted(ENDPOINTS.items()) for role in (*ROLES, None)]


@pytest.mark.parametrize("method, path, role", CASES, ids=[f"{m} {p} as {r or 'anonymous'}" for m, p, r in CASES])
def test_endpoint_x_role(method, path, role):
    perm = ENDPOINTS[(method, path)]
    target = make_user(("viewer",))  # a member other than the caller, for /admin/users/{user_id}/...
    with new_client() as client:
        if role is not None:
            login_as(client, make_user((role,)))
        status = _call(client, method, path, target).status_code

    if perm == SERVICE:
        assert status == 401, f"service endpoint must reject user/anonymous callers, got {status}"
        return
    if perm == PUBLIC:
        assert status not in (401, 403) or path in ("/oauth/token",), status
        return
    if role is None:
        assert status == 401, f"anonymous got {status}"
        return
    if perm == SESSION or role in PERMISSIONS[perm]:
        assert status not in (401, 403), f"{role} should be allowed {perm}, got {status}"
    else:
        assert status == 403, f"{role} must be denied {perm}, got {status}"
