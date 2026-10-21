"""In-process OpenID Provider for tests: discovery, JWKS, and a token endpoint that enforces PKCE.

The control plane talks to it through httpx.MockTransport, so the real OIDC client code runs unchanged.
Tests play the browser: follow /auth/login, "authenticate" here, then call /auth/callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test/realms/veyrqun"
CLIENT_ID = "veyrqun-dashboard"


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FakeIdP:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-kid-1"
        self.codes: dict[str, dict] = {}
        self.overrides: dict = {}      # claims to inject/override in the next id_token
        self.header_overrides: dict = {}

    # ---------------------------------------------------------------- endpoints

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
            })
        if path.endswith("/certs"):
            pub = self.key.public_key().public_numbers()
            return httpx.Response(200, json={"keys": [
                {"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256", "n": _b64u(pub.n), "e": _b64u(pub.e)}]})
        if path.endswith("/token"):
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            grant = self.codes.pop(code, None)
            if grant is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form["code_verifier"][0]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if challenge != grant["code_challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE failed"})
            return httpx.Response(200, json={"id_token": self.id_token(grant), "access_token": "opaque",
                                             "token_type": "Bearer"})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # ---------------------------------------------------------------- "browser" helpers

    def authenticate(self, authorize_url: str, *, sub: str, email: str, mfa: bool = True,
                     email_verified: bool = True) -> tuple[str, str]:
        q = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"sub": sub, "email": email, "nonce": q["nonce"], "code_challenge": q["code_challenge"],
                            "amr": ["pwd", "otp"] if mfa else ["pwd"], "email_verified": email_verified}
        return code, q["state"]

    def id_token(self, grant: dict) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": grant["sub"], "email": grant["email"],
                  "email_verified": grant["email_verified"], "nonce": grant["nonce"], "iat": now, "exp": now + 300,
                  "auth_time": now, "amr": grant["amr"], "sid": f"sid-{grant['sub']}", "name": "Test User"}
        claims.update(self.overrides)
        headers = {"kid": self.kid, **self.header_overrides}
        alg = headers.pop("alg", "RS256")
        if alg == "none":
            return jwt.encode(claims, None, algorithm="none", headers=headers)
        if alg == "HS256":
            return jwt.encode(claims, "shared-secret-attack", algorithm="HS256", headers=headers)
        return jwt.encode(claims, self.key, algorithm="RS256", headers=headers)

    def logout_token(self, sub: str, sid: str | None = None) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": sub, "iat": now, "exp": now + 120, "jti": secrets.token_hex(8),
                  "events": {"http://schemas.openid.net/event/backchannel-logout": {}}}
        if sid:
            claims["sid"] = sid
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


def b64_json(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode()
