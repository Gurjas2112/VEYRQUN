"""Provider-agnostic OpenID Connect relying party for the backend-for-frontend (BFF).

Authorization Code + PKCE (S256), state and nonce, confidential client. Tokens stay on the server.
ID tokens are verified strictly: signature via JWKS by `kid`, an algorithm allowlist (never `none` or HS*
from an external issuer), iss, aud, exp, nbf/iat, nonce. Works with Keycloak, Entra ID, Okta, Google.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from ..config import get_settings
from ..counters import client as redis_client

ALLOWED_ALGS = ("RS256", "PS256", "ES256", "EdDSA")
STATE_TTL_S = 600


class OIDCError(Exception):
    pass


@dataclass
class Identity:
    issuer: str
    sub: str
    email: str
    email_verified: bool
    name: str | None
    auth_time: int
    amr: list[str]
    acr: str | None
    aal: str | None
    sid: str | None

    @property
    def mfa(self) -> bool:
        settings = get_settings()
        mfa_amr = {m.strip() for m in settings.oidc_mfa_amr.split(",") if m.strip()}
        return bool(mfa_amr & set(self.amr)) or self.aal == "aal2" or (
            bool(settings.oidc_mfa_acr) and self.acr in {a.strip() for a in settings.oidc_mfa_acr.split(",")})


class Provider:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._discovery: dict | None = None
        self._jwks: dict | None = None
        self._jwks_at = 0.0

    def _http(self) -> httpx.Client:
        return httpx.Client(timeout=5.0, transport=self._transport)

    def discovery(self) -> dict:
        if self._discovery is None:
            s = get_settings()
            url = s.oidc_discovery_url or s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
            with self._http() as h:
                doc = h.get(url).raise_for_status().json()
            self._discovery = doc
        return self._discovery

    def _keys(self, force: bool = False) -> dict:
        if force or self._jwks is None or time.time() - self._jwks_at > 3600:
            with self._http() as h:
                self._jwks = h.get(self.discovery()["jwks_uri"]).raise_for_status().json()
            self._jwks_at = time.time()
        return self._jwks

    # ---------------------------------------------------------------- login

    def authorization_url(self, *, next_path: str, step_up: bool, invite: str | None) -> str:
        s = get_settings()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redis_client().set(f"vq:oidc:{state}", json.dumps(
            {"verifier": verifier, "nonce": nonce, "next": next_path, "step_up": step_up, "invite": invite,
             "at": int(time.time())}), ex=STATE_TTL_S)
        params = {
            "response_type": "code",
            "client_id": s.oidc_client_id,
            "redirect_uri": s.oidc_redirect_uri,
            "scope": s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if step_up:
            params.update({"prompt": "login", "max_age": "0"})
        endpoint = s.oidc_public_authorization_endpoint or self.discovery()["authorization_endpoint"]
        return f"{endpoint}?{urlencode(params)}"

    def complete(self, code: str, state: str) -> tuple[Identity, dict]:
        """Exchange the code (one-time state) and return the verified identity plus the login context."""
        raw = redis_client().getdel(f"vq:oidc:{state}")
        if not raw:
            raise OIDCError("unknown or expired state")
        ctx = json.loads(raw)
        s = get_settings()
        with self._http() as h:
            res = h.post(self.discovery()["token_endpoint"],
                         data={"grant_type": "authorization_code", "code": code, "redirect_uri": s.oidc_redirect_uri,
                               "code_verifier": ctx["verifier"]},
                         auth=(s.oidc_client_id, s.oidc_client_secret or ""))
        if res.status_code != 200:
            raise OIDCError(f"token exchange failed ({res.status_code})")
        claims = self.verify_id_token(res.json().get("id_token", ""), nonce=ctx["nonce"])
        if ctx["step_up"] and int(time.time()) - int(claims.get("auth_time", 0)) > 120:
            raise OIDCError("step-up requested but the IdP did not re-authenticate")
        return self._identity(claims), ctx

    def verify_id_token(self, token: str, *, nonce: str | None = None, audience: str | None = None) -> dict:
        s = get_settings()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCError("malformed id_token") from exc
        if header.get("alg") not in ALLOWED_ALGS:
            raise OIDCError(f"algorithm {header.get('alg')!r} not allowed")
        key = self._key_for(header.get("kid"))
        try:
            claims = jwt.decode(token, key=key, algorithms=list(ALLOWED_ALGS), audience=audience or s.oidc_client_id,
                                issuer=s.oidc_issuer, leeway=30,
                                options={"require": ["iss", "aud", "exp", "iat", "sub"]})
        except jwt.PyJWTError as exc:
            raise OIDCError(f"id_token rejected: {exc}") from exc
        if nonce is not None and claims.get("nonce") != nonce:
            raise OIDCError("nonce mismatch")
        return claims

    def _key_for(self, kid: str | None):
        for force in (False, True):  # refresh once on unknown kid (key rotation at the IdP)
            for jwk in self._keys(force).get("keys", []):
                if jwk.get("kid") == kid and jwk.get("use", "sig") == "sig":
                    return jwt.PyJWK(jwk).key
        raise OIDCError("signing key not found in IdP JWKS")

    def verify_logout_token(self, token: str) -> dict:
        claims = self.verify_id_token(token)
        if "http://schemas.openid.net/event/backchannel-logout" not in (claims.get("events") or {}):
            raise OIDCError("not a back-channel logout token")
        if "nonce" in claims:
            raise OIDCError("logout token must not contain a nonce")
        return claims

    @staticmethod
    def _identity(c: dict) -> Identity:
        amr = c.get("amr") or []
        return Identity(
            issuer=c["iss"], sub=c["sub"], email=(c.get("email") or "").strip().lower(),
            email_verified=bool(c.get("email_verified")), name=c.get("name") or c.get("preferred_username"),
            auth_time=int(c.get("auth_time") or c.get("iat")), amr=amr if isinstance(amr, list) else [amr],
            acr=c.get("acr"), aal=c.get("aal"), sid=c.get("sid"),
        )

    def end_session_url(self, post_logout_redirect: str) -> str | None:
        # Local logout must succeed even when the IdP is down; the IdP-side logout is best effort.
        try:
            endpoint = self.discovery().get("end_session_endpoint")
        except httpx.HTTPError:
            return None
        if not endpoint:
            return None
        return f"{endpoint}?{urlencode({'client_id': get_settings().oidc_client_id, 'post_logout_redirect_uri': post_logout_redirect})}"


_provider: Provider | None = None


def provider() -> Provider:
    global _provider
    if _provider is None:
        _provider = Provider()
    return _provider


def set_provider(p: Provider | None) -> None:
    """Tests inject a provider backed by an in-process fake IdP."""
    global _provider
    _provider = p
