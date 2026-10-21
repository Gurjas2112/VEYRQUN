"""Build the `veyrqun` Keycloak realm through the admin REST API, then (optionally) export it.

  python deploy/keycloak/configure_realm.py --base http://localhost:8180 [--export deploy/keycloak/realm-veyrqun.json]

The IdP only *authenticates* (password + OTP, NIST SP 800-63B password policy, brute-force lockout) and
reports how the user authenticated in the `amr` claim. Per-workspace roles live in VEYRQUN (iam.memberships),
so any OIDC provider (Entra ID, Okta, Google, Keycloak) can be swapped in without role mapping.
The export contains no secrets: the client secret is `${KC_DASHBOARD_CLIENT_SECRET}` (resolved at import).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

REALM = "veyrqun"
CLIENT = "veyrqun-dashboard"


class KC:
    def __init__(self, base: str, user: str, password: str) -> None:
        self.base = base.rstrip("/")
        tok = httpx.post(f"{self.base}/realms/master/protocol/openid-connect/token",
                         data={"client_id": "admin-cli", "username": user, "password": password,
                               "grant_type": "password"}).json()["access_token"]
        self.c = httpx.Client(base_url=f"{self.base}/admin/realms", headers={"Authorization": f"Bearer {tok}"},
                              timeout=30)

    def req(self, method: str, path: str, **kw) -> httpx.Response:
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400 and r.status_code != 409:
            sys.exit(f"{method} {path} -> {r.status_code} {r.text[:300]}")
        return r


def configure(kc: KC, redirect_uris: list[str], web_origins: list[str], backchannel_url: str) -> None:
    kc.req("POST", "", json={
        "realm": REALM, "enabled": True, "displayName": "VEYRQUN",
        "registrationAllowed": False, "loginWithEmailAllowed": True, "duplicateEmailsAllowed": False,
        "verifyEmail": True, "resetPasswordAllowed": True, "rememberMe": False,
        # NIST SP 800-63B: length, no composition rules, no forced rotation, argon2 hashing.
        "passwordPolicy": "length(12) and maxLength(128) and notUsername and notEmail and hashAlgorithm(argon2)",
        "bruteForceProtected": True, "permanentLockout": False, "failureFactor": 5,
        "waitIncrementSeconds": 60, "maxFailureWaitSeconds": 900, "quickLoginCheckMilliSeconds": 1000,
        "minimumQuickLoginWaitSeconds": 60,
        "otpPolicyType": "totp", "otpPolicyAlgorithm": "HmacSHA1", "otpPolicyDigits": 6, "otpPolicyPeriod": 30,
        "accessTokenLifespan": 300, "ssoSessionIdleTimeout": 1800, "ssoSessionMaxLifespan": 43200,
        "sslRequired": "external",
    })

    # Browser flow: cookie OR (password form, then OTP when the user has OTP configured).
    # Each step carries an AMR reference so the ID token says *how* the user authenticated.
    flow = "veyrqun-browser"
    kc.req("POST", f"/{REALM}/authentication/flows/browser/copy", json={"newName": flow})
    execs = kc.req("GET", f"/{REALM}/authentication/flows/{flow}/executions").json()
    for e in execs:
        provider = e.get("providerId")
        ref = {"auth-username-password-form": "pwd", "auth-otp-form": "otp"}.get(provider)
        if ref:
            # maxAge: how long this step still counts toward `amr`. Without it Keycloak only reports steps
            # that ran in the same second the token was issued, so `amr` comes back empty intermittently.
            kc.req("POST", f"/{REALM}/authentication/executions/{e['id']}/config",
                   json={"alias": f"{ref}-reference",
                         "config": {"default.reference.value": ref, "default.reference.maxAge": "43200"}})
        if provider in ("identity-provider-redirector",):
            kc.req("PUT", f"/{REALM}/authentication/flows/{flow}/executions", json={**e, "requirement": "DISABLED"})
    realm = kc.req("GET", f"/{REALM}").json()
    realm["browserFlow"] = flow
    kc.req("PUT", f"/{REALM}", json=realm)

    kc.req("POST", f"/{REALM}/clients", json={
        "clientId": CLIENT, "name": "VEYRQUN dashboard (BFF)", "protocol": "openid-connect",
        "publicClient": False, "secret": "${KC_DASHBOARD_CLIENT_SECRET}",
        "standardFlowEnabled": True, "directAccessGrantsEnabled": False, "implicitFlowEnabled": False,
        "serviceAccountsEnabled": False, "frontchannelLogout": False,
        "redirectUris": redirect_uris, "webOrigins": web_origins,
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "backchannel.logout.url": backchannel_url,
            "backchannel.logout.session.required": "true",
            "post.logout.redirect.uris": "+",
        },
        "protocolMappers": [
            {"name": "amr", "protocol": "openid-connect", "protocolMapper": "oidc-amr-mapper",
             "config": {"id.token.claim": "true", "access.token.claim": "true", "introspection.token.claim": "true"}},
            {"name": "acr", "protocol": "openid-connect", "protocolMapper": "oidc-acr-mapper",
             "config": {"id.token.claim": "true", "access.token.claim": "true"}},
        ],
    })


def export(kc: KC, out: Path) -> None:
    rep = kc.req("POST", f"/{REALM}/partial-export", params={"exportClients": "true",
                                                             "exportGroupsAndRoles": "true"}).json()
    for client in rep.get("clients", []):
        if client["clientId"] == CLIENT:
            client["secret"] = "${KC_DASHBOARD_CLIENT_SECRET}"  # noqa: S105 (placeholder resolved at import)
    out.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
    print(f"exported realm to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8180")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--redirect", action="append",
                    default=["http://localhost:8000/auth/callback", "http://localhost:5173/api/auth/callback"])
    ap.add_argument("--origin", action="append", default=["http://localhost:5173", "http://localhost:8000"])
    ap.add_argument("--backchannel", default="http://control-plane:8000/auth/backchannel-logout")
    ap.add_argument("--export", type=Path)
    a = ap.parse_args()
    kc = KC(a.base, a.admin, a.password)
    configure(kc, a.redirect, a.origin, a.backchannel)
    if a.export:
        export(kc, a.export)
