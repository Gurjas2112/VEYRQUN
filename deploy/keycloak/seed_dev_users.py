"""Seed DEV/TEST users into the local Keycloak realm (never used for real deployments).

  python deploy/keycloak/seed_dev_users.py --base http://localhost:8180 --out .dev-users.json

Passwords and TOTP secrets are generated randomly per run and written only to the git-ignored output
file, which the Playwright end-to-end tests read. Privileged users get an OTP credential, so Keycloak's
conditional-OTP step asks for it and the ID token's `amr` contains "otp".
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
from pathlib import Path

import httpx

REALM = "veyrqun"
USERS = [
    # (username, first name, needs OTP)
    ("admin", "Ada", True),
    ("secop", "Sam", True),
    ("approver", "Ari", True),
    ("auditor", "Aud", True),
    ("owner", "Ola", False),
    ("viewer", "Vic", False),
    ("newbie", "Neo", True),
    ("nomfa.operator", "Nia", False),
    ("invitee", "Ivy", True),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8180")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--out", type=Path, default=Path(".dev-users.json"))
    a = ap.parse_args()
    tok = httpx.post(f"{a.base}/realms/master/protocol/openid-connect/token",
                     data={"client_id": "admin-cli", "username": a.admin, "password": a.password,
                           "grant_type": "password"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    out, reps = {}, []
    for username, first, otp in USERS:
        password = secrets.token_urlsafe(18)
        email = f"{username}@veyrqun.dev"
        creds = [{"type": "password", "value": password, "temporary": False}]
        totp_secret = None
        if otp:
            raw = secrets.token_hex(10)  # Keycloak stores the raw secret; authenticators use base32(raw)
            totp_secret = base64.b32encode(raw.encode()).decode().rstrip("=")
            creds.append({
                "type": "otp", "userLabel": "seeded",
                "secretData": json.dumps({"value": raw}),
                "credentialData": json.dumps({"subType": "totp", "digits": 6, "counter": 0, "period": 30,
                                              "algorithm": "HmacSHA1"}),
            })
        reps.append({"username": username, "email": email, "firstName": first, "lastName": "Dev", "enabled": True,
                     "emailVerified": True, "credentials": creds})
        out[username] = {"email": email, "password": password, "totp_base32": totp_secret}
    r = httpx.post(f"{a.base}/admin/realms/{REALM}/partialImport", headers=h, timeout=30,
                   json={"ifResourceExists": "OVERWRITE", "users": reps})
    if r.status_code >= 300:
        sys.exit(f"partialImport failed: {r.status_code} {r.text[:300]}")
    for username in out:
        users = httpx.get(f"{a.base}/admin/realms/{REALM}/users", headers=h, params={"username": username,
                                                                                     "exact": "true"}).json()
        out[username]["sub"] = users[0]["id"]
    a.out.write_text(json.dumps(out, indent=2))
    print(f"seeded {len(out)} dev users -> {a.out} (git-ignored)")


if __name__ == "__main__":
    main()
