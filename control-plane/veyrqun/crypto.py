"""Envelope encryption (AES-256-GCM).

A master key (env `VEYRQUN_MASTER_KEY`, or a KMS in production) wraps per-purpose data keys;
data keys encrypt values. Every ciphertext carries the key id, so keys can rotate.
Format: b"v1." + kid + b"." + base64url(nonce || ciphertext||tag)
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoError(RuntimeError):
    pass


def _b64(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _unb64(data: bytes) -> bytes:
    return base64.urlsafe_b64decode(data + b"=" * (-len(data) % 4))


class KeyRing:
    def __init__(self, keys: dict[str, bytes], active_kid: str) -> None:
        for kid, key in keys.items():
            if len(key) != 32:
                raise CryptoError(f"key {kid!r} must be 32 bytes")
            if b"." in kid.encode():
                raise CryptoError("kid must not contain '.'")
        if active_kid not in keys:
            raise CryptoError("active kid not in key ring")
        self._keys = keys
        self.active_kid = active_kid

    @classmethod
    def from_env_value(cls, value: str) -> KeyRing:
        """`kid1:base64key,kid2:base64key` — the first entry is active."""
        keys: dict[str, bytes] = {}
        order: list[str] = []
        for part in value.split(","):
            kid, _, raw = part.strip().partition(":")
            keys[kid] = base64.b64decode(raw)
            order.append(kid)
        if not order:
            raise CryptoError("empty key ring")
        return cls(keys, order[0])

    def encrypt(self, plaintext: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self.active_kid]).encrypt(nonce, plaintext, aad)
        return b"v1." + self.active_kid.encode() + b"." + _b64(nonce + ct)

    def decrypt(self, token: bytes, aad: bytes = b"") -> bytes:
        try:
            version, kid, body = token.split(b".", 2)
        except ValueError as exc:
            raise CryptoError("malformed ciphertext") from exc
        if version != b"v1":
            raise CryptoError("unknown ciphertext version")
        key = self._keys.get(kid.decode())
        if key is None:
            raise CryptoError(f"unknown key id {kid.decode()!r}")
        raw = _unb64(body)
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad)


def generate_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode()
