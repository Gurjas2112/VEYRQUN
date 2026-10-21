"""Role matrix: the single source of truth for authorization.

Enforced server-side on every endpoint (auth/deps.py) and used to *generate* the endpoint x role
authorization tests (tests/test_authz_matrix.py). Hiding UI controls is never access control.
"""

from __future__ import annotations

ROLES = ("platform_admin", "security_operator", "approver", "auditor", "app_owner", "viewer")

# Roles that must have authenticated with MFA (amr contains otp/hwk, or aal2) to use any permission.
MFA_REQUIRED = frozenset({"platform_admin", "security_operator", "approver"})

# Separation of duties: an auditor is read-only and independent, so it can't also operate.
SOD_CONFLICTS = frozenset({
    frozenset({"auditor", "security_operator"}),
    frozenset({"auditor", "platform_admin"}),
    frozenset({"auditor", "approver"}),
    frozenset({"auditor", "app_owner"}),
})

ALL = frozenset(ROLES)
READERS = frozenset({"platform_admin", "security_operator", "approver", "auditor", "app_owner", "viewer"})

PERMISSIONS: dict[str, frozenset[str]] = {
    "agents:read": READERS,
    "agents:register": frozenset({"security_operator", "app_owner"}),
    "agents:rotate_secret": frozenset({"security_operator", "app_owner"}),
    "policy:read": READERS,
    # app_owner may *submit* changes; they always become a change request for an approver.
    "policy:edit": frozenset({"security_operator", "app_owner"}),
    "agents:control": frozenset({"security_operator"}),          # stop / resume / revoke / reinstate
    "fleet:stop": frozenset({"security_operator"}),
    "fleet:release": frozenset({"security_operator"}),           # production: needs an approver as well
    "spend:reset": frozenset({"security_operator"}),
    "actions:simulate": frozenset({"security_operator", "app_owner"}),
    "simulator:toggle": frozenset({"security_operator", "platform_admin"}),
    "approvals:read": frozenset({"approver", "security_operator", "auditor", "app_owner"}),
    "approvals:decide": frozenset({"approver"}),
    "audit:read": READERS,
    "audit:verify": frozenset({"auditor", "security_operator", "platform_admin"}),
    "audit:export": frozenset({"auditor"}),
    "metrics:read": READERS,
    "iam:read": frozenset({"platform_admin", "auditor"}),
    "iam:manage": frozenset({"platform_admin"}),
    "keys:rotate": frozenset({"platform_admin"}),
}

# Permissions that need a fresh authentication (OIDC max_age / auth_time) right before use.
STEP_UP = frozenset({"fleet:stop", "fleet:release", "keys:rotate", "iam:manage"})


def permissions_for(roles: set[str] | frozenset[str]) -> frozenset[str]:
    return frozenset(p for p, allowed in PERMISSIONS.items() if roles & allowed)


def sod_violation(roles: set[str]) -> frozenset[str] | None:
    for pair in SOD_CONFLICTS:
        if pair <= roles:
            return pair
    return None
