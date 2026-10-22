"""Audit writer: the single consumer that turns gateway decision events into hash-chained ledger entries.

  python -m veyrqun.audit_writer            # run forever (Render worker / Kubernetes Deployment)
  python -m veyrqun.audit_writer --once     # drain what's there, anchor, exit (cron, tests)

Delivery is at-least-once (Redis Streams consumer group); the ledger makes it effectively exactly-once, because
`audit.append` is idempotent on decision_id. Ordering:
  1. XAUTOCLAIM messages another consumer left pending for longer than AUDIT_CLAIM_IDLE_MS (crash recovery);
  2. XREADGROUP new messages;
  3. for each: append in its own transaction, then XACK. If the DB is down nothing is acked and it retries.
Malformed events and events that fail AUDIT_MAX_DELIVERIES times go to the dead-letter stream (never silently dropped).

Only one replica writes at a time: a Redis lease (SET NX PX, renewed by compare-and-extend). The chain is global
and linear, so parallel writers would only queue on the advisory lock anyway (ADR 0012).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import time
from datetime import datetime
from uuid import UUID

import redis
from sqlalchemy import text

from . import audit, counters
from .anchor_store import s3_put
from .config import get_settings
from .db import tx
from .money import check_currency, from_minor

log = logging.getLogger("veyrqun.audit_writer")

STATUS_KEY = "vq:audit-writer:status"
LEADER_KEY = "vq:audit-writer:leader"
_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class BadEvent(ValueError):
    """The event can never be written (schema violation): dead-letter it immediately."""


def parse_event(fields: dict) -> dict:
    """Validate a gateway DecisionEvent and map it to `audit.append` arguments."""
    try:
        e = json.loads(fields["event"])
        currency = check_currency(e["currency"])
        amount_minor = e["amount_minor"]
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor < 0:
            raise BadEvent("amount_minor must be a non-negative integer")
        if e["mode"] not in ("observe", "enforce"):
            raise BadEvent(f"unknown mode {e['mode']!r}")
        decision_id = str(e["decision_id"])
        if not decision_id or len(decision_id) > 128:
            raise BadEvent("bad decision_id")
        details = {k: e[k] for k in ("effective_outcome", "bundle_revision", "data_classes", "signals", "redactions", "approval_id",
                                     "upstream_status", "stream_aborted", "request_sha256", "response_sha256",
                                     "timings_ms", "payload") if e.get(k) is not None}
        details["source"] = "gateway"
        return {
            "workspace_id": UUID(e["workspace_id"]),
            "actor": str(e["actor"])[:256],
            "agent_id": str(e["agent_id"])[:128],
            "action_type": str(e["action"])[:128],
            "amount": from_minor(amount_minor, currency),
            "currency": currency,
            "outcome": str(e["outcome"]),
            "rule_id": e.get("rule_id"),
            "policy_version": int(e["policy_version"]),
            "decision_id": decision_id,
            "mode": e["mode"],
            "details": details,
            "ts": datetime.fromisoformat(e["ts"].replace("Z", "+00:00")),
        }
    except BadEvent:
        raise
    except (KeyError, TypeError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        raise BadEvent(f"{type(exc).__name__}: {exc}") from exc


class AuditWriter:
    def __init__(self, r: redis.Redis | None = None, consumer: str | None = None, put=None):
        s = get_settings()
        self.s = s
        # Own connection: the shared client's 0.5 s socket timeout would cut off blocking XREADGROUP calls.
        self.r = r or redis.Redis.from_url(s.redis_url, socket_timeout=10, socket_connect_timeout=2, decode_responses=True)
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.put = put if put is not None else s3_put()
        self.stats = {"written": 0, "duplicates": 0, "dead_lettered": 0, "errors": 0, "anchors": 0}
        self._last_anchor = 0.0
        self._stop = False

    # ------------------------------------------------------------ leadership

    def acquire(self) -> bool:
        ttl = self.s.audit_leader_ttl_ms
        if self.r.set(LEADER_KEY, self.consumer, nx=True, px=ttl):
            return True
        return bool(self.r.eval(_RENEW, 1, LEADER_KEY, self.consumer, ttl))

    def release(self) -> None:
        self.r.eval(_RELEASE, 1, LEADER_KEY, self.consumer)

    # ------------------------------------------------------------ consuming

    def ensure_group(self) -> None:
        try:
            self.r.xgroup_create(self.s.audit_stream, self.s.audit_group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def _dead_letter(self, msg_id: str, fields: dict, reason: str) -> None:
        self.r.xadd(self.s.audit_dlq_stream, {**fields, "original_id": msg_id, "reason": reason[:500],
                                              "consumer": self.consumer}, maxlen=100_000, approximate=True)
        self.r.xack(self.s.audit_stream, self.s.audit_group, msg_id)
        self.stats["dead_lettered"] += 1
        log.error("dead-lettered %s: %s", msg_id, reason)

    def _deliveries(self, msg_id: str) -> int:
        pending = self.r.xpending_range(self.s.audit_stream, self.s.audit_group, min=msg_id, max=msg_id, count=1)
        return pending[0]["times_delivered"] if pending else 1

    def handle(self, msg_id: str, fields: dict) -> bool:
        """Write one message. Returns True when it was acked (written, duplicate or dead-lettered)."""
        try:
            args = parse_event(fields)
        except BadEvent as exc:
            self._dead_letter(msg_id, fields, f"invalid event: {exc}")
            return True
        try:
            with tx(args["workspace_id"]) as conn:
                if conn.execute(text("SELECT 1 FROM iam.workspaces WHERE id = :w"), {"w": args["workspace_id"]}).first() is None:
                    raise BadEvent("unknown workspace")
                entry = audit.append(conn, **args)
        except BadEvent as exc:
            self._dead_letter(msg_id, fields, str(exc))
            return True
        except Exception as exc:  # DB down, unknown workspace, ...: retry later, dead-letter if it keeps failing
            self.stats["errors"] += 1
            if self._deliveries(msg_id) >= self.s.audit_max_deliveries:
                self._dead_letter(msg_id, fields, f"failed {self.s.audit_max_deliveries} times: {type(exc).__name__}: {exc}")
                return True
            log.warning("write failed for %s (will retry): %s", msg_id, exc)
            return False
        self.r.xack(self.s.audit_stream, self.s.audit_group, msg_id)
        self.stats["duplicates" if entry.get("duplicate") else "written"] += 1
        return True

    def step(self, block_ms: int = 1000, count: int = 100) -> int:
        """One poll: reclaim stuck messages, then read new ones. Returns how many messages were handled."""
        handled = 0
        _, claimed, _ = self.r.xautoclaim(self.s.audit_stream, self.s.audit_group, self.consumer,
                                          min_idle_time=self.s.audit_claim_idle_ms, start_id="0-0", count=count)
        # Our own unacked messages (from a failed write in this process) are retried immediately.
        own = self.r.xreadgroup(self.s.audit_group, self.consumer, {self.s.audit_stream: "0"}, count=count)
        backlog = [m for m in claimed if m[1]] + [m for _, msgs in own for m in msgs if m[1]]
        seen = set()
        for msg_id, fields in backlog:
            if msg_id not in seen:
                seen.add(msg_id)
                handled += self.handle(msg_id, fields)
        if backlog and handled < len(seen):
            time.sleep(min(1.0, block_ms / 1000))  # the DB is failing; don't spin
            return handled
        new = self.r.xreadgroup(self.s.audit_group, self.consumer, {self.s.audit_stream: ">"}, count=count, block=block_ms)
        for _, msgs in new or []:
            for msg_id, fields in msgs:
                handled += self.handle(msg_id, fields)
        return handled

    def maybe_anchor(self, force: bool = False) -> dict | None:
        if not force and time.monotonic() - self._last_anchor < self.s.audit_anchor_interval_s:
            return None
        self._last_anchor = time.monotonic()
        try:
            anchor = audit.create_anchor(self.put)
        except Exception as exc:
            log.error("anchoring failed: %s", exc)
            return None
        if anchor:
            self.stats["anchors"] += 1
            log.info("anchored seq %s (%s)", anchor["seq"], anchor["external_ref"] or "db only")
        return anchor

    def publish_status(self, leader: bool) -> None:
        self.r.hset(STATUS_KEY, mapping={"consumer": self.consumer, "leader": int(leader),
                                         "heartbeat": int(time.time()), **self.stats})
        self.r.expire(STATUS_KEY, 120)

    # ------------------------------------------------------------ loops

    def drain(self) -> dict:
        """Process everything currently in the stream, anchor, return stats (for --once, cron and tests)."""
        self.ensure_group()
        if not self.acquire():
            return {**self.stats, "leader": False}
        try:
            while self.step(block_ms=1) > 0:
                pass
            self.maybe_anchor(force=True)
            self.publish_status(True)
        finally:
            self.release()
        return {**self.stats, "leader": True}

    def run(self) -> None:
        self.ensure_group()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_stop", True))
        log.info("audit writer %s started", self.consumer)
        while not self._stop:
            try:
                leader = self.acquire()
                if leader:
                    self.step()
                    self.maybe_anchor()
                else:
                    time.sleep(1)
                self.publish_status(leader)
            except redis.RedisError as exc:
                log.error("redis unavailable: %s", exc)
                time.sleep(2)
        self.release()


def status(r: redis.Redis | None = None) -> dict:
    """Backlog (not yet delivered), pending (delivered, not acked), DLQ depth and writer heartbeat."""
    s = get_settings()
    r = r or counters.client()
    out = {"stream": s.audit_stream, "group": s.audit_group, "backlog": None, "pending": None, "dead_letters": 0,
           "writer": None}
    try:
        for g in r.xinfo_groups(s.audit_stream):
            if g["name"] == s.audit_group:
                out["backlog"], out["pending"] = g.get("lag"), g["pending"]
        if out["backlog"] is None:
            out["backlog"] = r.xlen(s.audit_stream)
    except redis.ResponseError:
        out["backlog"], out["pending"] = 0, 0  # no stream yet
    out["dead_letters"] = r.xlen(s.audit_dlq_stream)
    w = r.hgetall(STATUS_KEY)
    if w:
        out["writer"] = {k: (int(v) if v.lstrip("-").isdigit() else v) for k, v in w.items()}
        out["writer"]["heartbeat_age_s"] = int(time.time()) - out["writer"]["heartbeat"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="drain, anchor and exit")
    a = ap.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    w = AuditWriter()
    if a.once:
        print(json.dumps(w.drain()))
    else:
        w.run()


if __name__ == "__main__":
    main()

