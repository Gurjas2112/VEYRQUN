"""Signing keys (Ed25519) for agent JWTs and policy bundles, published as JWKS.

Private keys are stored encrypted (AES-256-GCM envelope, see crypto.py) and rotate by kid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import text

from .config import get_settings
from .crypto import KeyRing
from .db import tx

log = logging.getLogger("veyrqun.keys")

# Fixed, public, dev/test-only key. Production refuses to start without VEYRQUN_MASTER_KEY (config.py).
_DEV_MASTER = "dev:" + base64.b64encode(hashlib.sha256(b"veyrqun-insecure-dev-master-key").digest()).decode()


@lru_cache(maxsize=1)
def keyring() -> KeyRing:
    value = get_settings().veyrqun_master_key
    if not value:
        log.warning("VEYRQUN_MASTER_KEY not set; using the INSECURE dev master key")
        value = _DEV_MASTER
    return KeyRing.from_env_value(value)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _public_jwk(kid: str, pub: Ed25519PublicKey, use: str) -> dict:
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": use, "kid": kid, "x": _b64u(raw)}


def _create(purpose: str) -> tuple[str, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    kid = f"{purpose}-{int(time.time())}-{secrets.token_hex(3)}"
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with tx() as conn:
        conn.execute(
            text(
                "INSERT INTO iam.signing_keys (kid, purpose, alg, public_jwk, private_enc) "
                "VALUES (:kid, :purpose, 'EdDSA', CAST(:jwk AS jsonb), :enc)"
            ),
            {"kid": kid, "purpose": purpose, "enc": keyring().encrypt(pem, aad=kid.encode()),
             "jwk": json.dumps(_public_jwk(kid, key.public_key(), "sig"))},
        )
    return kid, key


_cache: dict[str, tuple[str, Ed25519PrivateKey]] = {}


def active_key(purpose: str) -> tuple[str, Ed25519PrivateKey]:
    if purpose in _cache:
        return _cache[purpose]
    with tx() as conn:
        row = conn.execute(
            text(
                "SELECT kid, private_enc FROM iam.signing_keys WHERE purpose = :p AND retired_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"p": purpose},
        ).first()
    if row is None:
        kid, key = _create(purpose)
    else:
        pem = keyring().decrypt(bytes(row.private_enc), aad=row.kid.encode())
        kid, key = row.kid, serialization.load_pem_private_key(pem, password=None)
    _cache[purpose] = (kid, key)
    return kid, key


def rotate(purpose: str) -> str:
    """Retire the active key (it stays in JWKS for verification) and create a new one."""
    with tx() as conn:
        conn.execute(text("UPDATE iam.signing_keys SET retired_at = now() WHERE purpose = :p AND retired_at IS NULL"),
                     {"p": purpose})
    _cache.pop(purpose, None)
    return active_key(purpose)[0]


def jwks() -> dict:
    """Active keys plus keys retired in the last 24 h, so in-flight tokens still verify."""
    with tx() as conn:
        rows = conn.execute(
            text(
                "SELECT public_jwk FROM iam.signing_keys WHERE purpose IN ('agent_jwt', 'bundle') "
                "AND (retired_at IS NULL OR retired_at > now() - interval '24 hours') ORDER BY created_at"
            )
        ).all()
    return {"keys": [r.public_jwk for r in rows]}


def issue_agent_token(workspace_id: str, agent_key: str, scopes: list[str], env: str) -> tuple[str, int]:
    settings = get_settings()
    kid, key = active_key("agent_jwt")
    now = int(time.time())
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.agent_token_audience,
        "sub": agent_key,
        "ws": workspace_id,
        "env": env,
        "scope": " ".join(scopes),
        "iat": now,
        "nbf": now,
        "exp": now + settings.agent_token_ttl_s,
        "jti": secrets.token_hex(12),
    }
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid}), settings.agent_token_ttl_s


def sign_bytes(purpose: str, payload: bytes) -> tuple[str, str]:
    kid, key = active_key(purpose)
    return kid, _b64u(key.sign(payload))


def new_client_secret() -> tuple[str, str]:
    """High-entropy secret (shown once) and its SHA-256 hash (stored). A slow KDF isn't needed
    for 256-bit random secrets; it would only add latency to every token request."""
    secret = "vqs_" + secrets.token_urlsafe(32)
    return secret, hashlib.sha256(secret.encode()).hexdigest()


def check_client_secret(secret: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(hashlib.sha256(secret.encode()).hexdigest(), stored_hash)


def reset_cache() -> None:
    _cache.clear()
    keyring.cache_clear()
