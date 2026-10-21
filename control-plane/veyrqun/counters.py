"""Live counters in Redis: atomic reserve/release via the shared Lua scripts.

Keys carry the agent's local date, so the daily cap rolls over at local midnight in each
agent's timezone with no scheduled job. `{ws:agent}` is a hash tag so every key for one agent
lands on the same Redis Cluster slot (required for multi-key scripts).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import redis

from .config import get_settings
from .ids import uuid7


class CounterUnavailable(RuntimeError):
    """Redis is unreachable: callers must fail closed for anything that carries spend/rate state."""


@lru_cache(maxsize=4)
def _client(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5, decode_responses=True)


def client() -> redis.Redis:
    return _client(get_settings().redis_url)


@lru_cache(maxsize=1)
def _scripts():
    lua_dir = get_settings().lua_dir
    c = client()
    return (
        c.register_script((lua_dir / "reserve.lua").read_text()),
        c.register_script((lua_dir / "release.lua").read_text()),
    )


def local_date(tz: str, now: datetime | None = None) -> str:
    return (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def keys(workspace_id: str, agent_key: str, action: str, tz: str) -> list[str]:
    tag = f"{{{workspace_id}:{agent_key}}}"
    day = local_date(tz)
    return [f"vq:{tag}:spend:{day}", f"vq:{tag}:aspend:{action}:{day}", f"vq:{tag}:rate"]


def reserve(workspace_id: str, agent_key: str, action: str, tz: str, amount_minor: int, limits: dict) -> tuple[bool, str | None, str]:
    """Returns (ok, rule_id_if_blocked, reservation_id)."""
    settings = get_settings()
    reserve_script, _ = _scripts()
    reservation = str(uuid7())

    def lim(v: int | None) -> int:
        return -1 if v is None else int(v)

    try:
        ok, rule, _spend = reserve_script(
            keys=keys(workspace_id, agent_key, action, tz),
            args=[
                amount_minor,
                lim(limits.get("spend_cap_minor")),
                lim(limits.get("action_daily_cap_minor")),
                lim(limits.get("rate_limit_per_minute")),
                int(time.time() * 1000),
                settings.rate_window_ms,
                reservation,
                settings.counter_ttl_s,
            ],
        )
    except redis.RedisError as exc:
        raise CounterUnavailable(str(exc)) from exc
    return bool(ok), (rule or None), reservation


def release(workspace_id: str, agent_key: str, action: str, tz: str, amount_minor: int, reservation: str) -> bool:
    _, release_script = _scripts()
    try:
        return bool(release_script(keys=keys(workspace_id, agent_key, action, tz), args=[amount_minor, reservation]))
    except redis.RedisError as exc:
        raise CounterUnavailable(str(exc)) from exc


def spend_today_minor(workspace_id: str, agent_key: str, tz: str) -> int:
    try:
        raw = client().get(keys(workspace_id, agent_key, "_", tz)[0])
    except redis.RedisError as exc:
        raise CounterUnavailable(str(exc)) from exc
    return int(raw or 0)


def reset_spend(workspace_id: str, agent_key: str, tz: str) -> None:
    tag = f"{{{workspace_id}:{agent_key}}}"
    day = local_date(tz)
    c = client()
    try:
        c.delete(f"vq:{tag}:spend:{day}")
        for key in c.scan_iter(match=f"vq:{tag}:aspend:*:{day}", count=200):
            c.delete(key)
    except redis.RedisError as exc:
        raise CounterUnavailable(str(exc)) from exc


def bundle_revision() -> int | None:
    """Global policy revision, used to invalidate process caches across instances."""
    try:
        raw = client().get("vq:bundle:revision")
    except redis.RedisError:
        return None
    return int(raw) if raw is not None else None


def publish_revision(revision: int) -> None:
    try:
        c = client()
        c.set("vq:bundle:revision", revision)
        c.publish("veyrqun:control", f'{{"type":"bundle","revision":{revision}}}')
    except redis.RedisError:
        # Gateways also poll the bundle endpoint with ETag, so a missed publish only delays propagation.
        pass


def set_fleet_state(workspace_id: str, stopped: bool, version: int) -> None:
    """Gateways poll this key every second as the backstop to the pub/sub kill-switch message."""
    try:
        client().set(f"vq:fleet:{workspace_id}", json.dumps({"stopped": stopped, "version": version}))
    except redis.RedisError:
        pass


def publish_control(message: str) -> None:
    try:
        client().publish("veyrqun:control", message)
    except redis.RedisError:
        pass
