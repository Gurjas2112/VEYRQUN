"""Brute-force protection for the agent token endpoint (credential stuffing against client secrets).

Failures are counted per client id and per source IP in a fixed 15-minute window. Over the limit, the endpoint
answers 429 before the secret is even checked, so guessing gets no feedback. A success clears the client counter.
If Redis is down the guard fails open for this check only: token issuance itself still needs a valid secret.
"""

from __future__ import annotations

import hashlib
import logging

import redis

from .counters import client

log = logging.getLogger("veyrqun.token_guard")

WINDOW_S = 900
MAX_PER_CLIENT = 10
MAX_PER_IP = 50


def keys(client_id: str, ip: str) -> tuple[str, str]:
    cid = hashlib.sha256(client_id.encode()).hexdigest()[:32]  # never store raw identifiers as Redis keys
    return f"vq:tokfail:c:{cid}", f"vq:tokfail:ip:{ip}"


def locked(k: tuple[str, str]) -> bool:
    try:
        c, i = client().mget(k[0], k[1])
    except redis.RedisError:
        return False
    return int(c or 0) >= MAX_PER_CLIENT or int(i or 0) >= MAX_PER_IP


def failed(k: tuple[str, str]) -> None:
    try:
        pipe = client().pipeline()
        for key in k:
            pipe.incr(key)
            pipe.expire(key, WINDOW_S, nx=True)
        pipe.execute()
    except redis.RedisError as exc:
        log.warning("failed-attempt counter unavailable (%s); lockout not enforced", type(exc).__name__)


def succeeded(k: tuple[str, str]) -> None:
    try:
        client().delete(k[0])
    except redis.RedisError:
        pass
