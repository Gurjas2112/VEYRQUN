"""Phase 3: signed audit pipeline. HMAC per entry, idempotent writer, dead letters, anchors, key rotation."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from veyrqun import anchor_store, audit, audit_writer, keys, service
from veyrqun.ids import uuid7

STREAM = "vq:decisions"


def _activity(n=6):
    for i in range(n):
        service.attempt_action("travel_agent_01", "flight_rebook", Decimal(str(10 + i)))


def _as_dba(owner_engine, *statements):
    """An attacker with owner rights who disables the append-only triggers first."""
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit.events DISABLE TRIGGER events_append_only"))
        for sql, params in statements:
            conn.execute(text(sql), params or {})
        conn.execute(text("ALTER TABLE audit.events ENABLE TRIGGER events_append_only"))


def _event(**over):
    e = {"decision_id": str(uuid7()), "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
         "workspace_id": str(service.default_workspace()), "actor": "agent:travel_agent_01",
         "agent_id": "travel_agent_01", "action": "llm.chat", "amount_minor": 1234, "currency": "USD",
         "outcome": "allow", "effective_outcome": "allow", "rule_id": None, "mode": "enforce", "policy_version": 1,
         "bundle_revision": 3, "data_classes": ["email"], "redactions": ["email"], "upstream_status": 200,
         "request_sha256": "ab" * 32, "timings_ms": {"policy": 0.2}}
    e.update(over)
    return e


@pytest.fixture
def writer():
    w = audit_writer.AuditWriter(consumer="test-writer", put=None)
    w.ensure_group()
    return w


def _xadd(w, event):
    return w.r.xadd(STREAM, {"event": json.dumps(event) if isinstance(event, dict) else event})


def _gateway_entries():
    return [e for e in audit.query(service.default_workspace(), limit=200) if e["details"].get("source") == "gateway"]


# ---------------------------------------------------------------- ledger


def test_new_entries_are_v2_and_hmac_signed():
    _activity(2)
    e = audit.query(service.default_workspace(), limit=1)[0]
    assert e["hash_version"] == 2 and len(e["hmac"]) == 64 and e["hmac_kid"].startswith("audit_hmac-")
    assert audit.verify_entry(e)["valid"] is True


def test_details_are_covered_by_the_hash(writer, owner_engine):
    _xadd(writer, _event())
    writer.drain()
    entry = _gateway_entries()[0]
    _as_dba(owner_engine, ("UPDATE audit.events SET details = jsonb_set(details, '{data_classes}', '[]') WHERE id = :id",
                           {"id": entry["id"]}))
    result = audit.verify_chain()
    assert result["valid"] is False and result["reason"] == "entry_hash invalid" and result["broken_at"] == entry["id"]


def test_append_is_idempotent_on_decision_id():
    ws = service.default_workspace()
    from veyrqun.db import tx

    args = dict(workspace_id=ws, actor="t", agent_id="a", action_type="x", amount=Decimal("1"), currency="USD",
                outcome="allow", rule_id=None, policy_version=1, decision_id="dec-1")
    with tx(ws) as conn:
        first = audit.append(conn, **args)
    with tx(ws) as conn:
        again = audit.append(conn, **{**args, "amount": Decimal("999")})
    assert again["duplicate"] and again["seq"] == first["seq"] and again["amount"] == "1.00"
    assert audit.verify_chain()["entries_checked"] == 1


# ---------------------------------------------------------------- writer


def test_writer_records_gateway_decisions_with_details(writer):
    ev = _event()
    _xadd(writer, ev)
    stats = writer.drain()
    assert stats["written"] == 1 and stats["leader"]
    [entry] = _gateway_entries()
    assert entry["decision_id"] == ev["decision_id"] and entry["amount"] == "12.34"
    assert entry["details"]["redactions"] == ["email"] and entry["details"]["bundle_revision"] == 3
    assert writer.r.xpending(STREAM, "audit-writer")["pending"] == 0
    assert audit.verify_chain()["valid"]


def test_redelivered_event_is_written_once(writer):
    ev = _event()
    _xadd(writer, ev)
    _xadd(writer, ev)  # e.g. the gateway retried the XADD, or the writer crashed between commit and XACK
    stats = writer.drain()
    assert (stats["written"], stats["duplicates"]) == (1, 1)
    assert len(_gateway_entries()) == 1


def test_messages_left_by_a_crashed_writer_are_reclaimed(writer, monkeypatch):
    _xadd(writer, _event())
    crashed = audit_writer.AuditWriter(consumer="crashed", put=None)
    crashed.r.xreadgroup("audit-writer", "crashed", {STREAM: ">"}, count=10)  # read, never acked
    assert writer.r.xpending(STREAM, "audit-writer")["pending"] == 1
    monkeypatch.setattr(writer.s, "audit_claim_idle_ms", 0)
    assert writer.drain()["written"] == 1
    assert writer.r.xpending(STREAM, "audit-writer")["pending"] == 0


@pytest.mark.parametrize("bad", ["not json", "missing fields", "fractional amount", "negative amount", "bad mode"])
def test_malformed_events_are_dead_lettered(writer, bad):
    payload = {"not json": "not json", "missing fields": json.dumps({"decision_id": "x"}),
               "fractional amount": json.dumps(_event(amount_minor=1.5)),
               "negative amount": json.dumps(_event(amount_minor=-1)),
               "bad mode": json.dumps(_event(mode="yolo"))}[bad]
    _xadd(writer, payload)
    stats = writer.drain()
    assert stats["dead_lettered"] == 1 and stats["written"] == 0
    [(_, dlq)] = writer.r.xrange("vq:decisions:dlq")
    assert dlq["event"] == payload and dlq["reason"].startswith("invalid event")
    assert writer.r.xpending(STREAM, "audit-writer")["pending"] == 0


def test_unknown_workspace_is_dead_lettered(writer):
    _xadd(writer, _event(workspace_id=str(uuid7())))
    assert writer.drain()["dead_lettered"] == 1
    assert writer.r.xrange("vq:decisions:dlq")[0][1]["reason"] == "unknown workspace"


def test_db_failures_retry_then_dead_letter(writer, monkeypatch):
    monkeypatch.setattr(writer.s, "audit_max_deliveries", 3)

    def boom(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(audit, "append", boom)
    _xadd(writer, _event())
    writer.ensure_group()
    assert writer.acquire()
    writer.step(block_ms=1)
    assert writer.r.xpending(STREAM, "audit-writer")["pending"] == 1  # not acked: retried, not lost
    for _ in range(3):
        writer.step(block_ms=1)
    assert writer.stats["dead_lettered"] == 1
    assert "failed 3 times" in writer.r.xrange("vq:decisions:dlq")[0][1]["reason"]


def test_only_one_writer_holds_the_lease(writer):
    other = audit_writer.AuditWriter(consumer="other", put=None)
    assert writer.acquire() and not other.acquire()
    assert other.drain()["leader"] is False
    writer.release()
    assert other.acquire()
    other.release()


def test_status_reports_backlog_and_dead_letters(writer):
    for _ in range(3):
        _xadd(writer, _event())
    _xadd(writer, "garbage")
    before = audit_writer.status(writer.r)
    assert before["backlog"] == 4
    writer.drain()
    after = audit_writer.status(writer.r)
    assert (after["backlog"], after["pending"], after["dead_letters"]) == (0, 0, 1)
    assert after["writer"]["written"] == 3 and after["writer"]["heartbeat_age_s"] <= 2


# ---------------------------------------------------------------- key rotation


def test_hmac_key_rotation_keeps_old_entries_verifiable():
    _activity(3)
    old_kid = keys.active_hmac()[0]
    new_kid = keys.rotate("audit_hmac")
    keys.reset_cache()
    _activity(3)
    kids = {e["hmac_kid"] for e in audit.query(service.default_workspace(), limit=50)}
    assert new_kid != old_kid and kids == {old_kid, new_kid}
    assert audit.verify_chain()["valid"]


def test_unknown_hmac_kid_fails_verification(owner_engine):
    _activity(3)
    e = audit.query(service.default_workspace(), limit=1)[0]
    _as_dba(owner_engine, ("UPDATE audit.events SET hmac_kid = 'audit_hmac-forged' WHERE id = :id", {"id": e["id"]}))
    assert audit.verify_chain()["reason"] == "hmac invalid"


# ---------------------------------------------------------------- anchors


def test_anchor_is_signed_and_skipped_when_head_unchanged():
    _activity(3)
    a = audit.create_anchor()
    assert a["seq"] == 3 and a["entries"] == 3 and a["kid"].startswith("audit_anchor-")
    assert audit.create_anchor() is None
    assert audit.verify_chain()["anchors_checked"] == 1
    # The anchor public key is published, so anyone can check the signature offline.
    assert any(k["kid"] == a["kid"] for k in keys.jwks()["keys"])


def test_anchor_copy_goes_off_database():
    stored = {}
    _activity(2)
    a = audit.create_anchor(lambda name, body: stored.setdefault(name, body) and f"mem://{name}")
    doc = json.loads(stored["anchors/000000000002.json"])
    assert a["external_ref"] == "mem://anchors/000000000002.json"
    assert doc["payload"] == {"seq": 2, "head_hash": a["head_hash"], "entries": 2} and doc["kid"] == a["kid"]


def test_full_rewrite_with_stolen_hmac_key_is_caught_by_the_anchor(owner_engine):
    """Worst case: the attacker has DB owner rights AND the HMAC secret, and re-signs the whole tail."""
    _activity(6)
    audit.create_anchor()
    with owner_engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM audit.events ORDER BY seq")).all()
    entries = [audit.row_to_entry(r) for r in rows]
    flipped = "block" if entries[2]["outcome"] == "allow" else "allow"
    entries[2] = {**entries[2], "outcome": flipped, "rule_id": "forged"}
    statements, prev = [], entries[1]["entry_hash"]
    for e in entries[2:]:
        e["prev_hash"] = prev
        e["entry_hash"] = audit.compute_entry_hash(e)
        e["hmac"] = audit.compute_hmac(e["entry_hash"], keys.hmac_secret(e["hmac_kid"]))
        statements.append(("UPDATE audit.events SET outcome = :o, rule_id = :r, prev_hash = :p, entry_hash = :h, "
                           "hmac = :m WHERE id = :id", {"o": e["outcome"], "r": e["rule_id"], "p": e["prev_hash"],
                                                         "h": e["entry_hash"], "m": e["hmac"], "id": e["id"]}))
        prev = e["entry_hash"]
    _as_dba(owner_engine, *statements)
    result = audit.verify_chain()
    assert result["valid"] is False and "anchor" in result["reason"] and result["seq"] == 6


def test_deleting_the_tail_is_caught_by_the_anchor(owner_engine):
    _activity(5)
    audit.create_anchor()
    _as_dba(owner_engine, ("DELETE FROM audit.events WHERE seq > 3", None))
    result = audit.verify_chain()
    assert result["valid"] is False and "shorter" in result["reason"]


def test_forged_anchor_is_rejected(owner_engine):
    _activity(2)
    a = audit.create_anchor()
    with owner_engine.begin() as conn:
        conn.execute(text("INSERT INTO audit.anchors (seq, head_hash, entries, signature, kid) "
                          "VALUES (2, :h, 2, :sig, :kid)"),
                     {"h": "0" * 64, "sig": "A" * 86, "kid": a["kid"]})
    assert audit.verify_chain()["reason"] == "anchor signature invalid"


def test_anchors_are_append_only(owner_engine):
    _activity(1)
    audit.create_anchor()
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM audit.anchors"))


# ---------------------------------------------------------------- S3 signing


def test_sigv4_signing_key_matches_aws_reference_vector():
    # AWS docs, "Examples of how to derive a signing key for Signature Version 4".
    key = anchor_store.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20120215", "us-east-1", "iam")
    assert key.hex() == "f4780e2d9f65fa895f9c67b32ce1baf0b0d8a43505a000a1a9e090d414db404d"


def test_sigv4_matches_aws_put_object_example():
    # AWS docs, "Signature Calculations for the Authorization Header: Example: PUT Object".
    h = anchor_store.sigv4_headers(
        "PUT", "https://examplebucket.s3.amazonaws.com/test$file.text", b"Welcome to Amazon S3.",
        access_key="AKIAIOSFODNN7EXAMPLE", secret="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1",
        headers={"date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
        now=datetime(2013, 5, 24, tzinfo=UTC))
    assert h["x-amz-content-sha256"] == "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"
    assert h["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, "
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")


def test_sigv4_headers_sign_the_body():
    now = datetime(2026, 10, 22, tzinfo=UTC)
    h = anchor_store.sigv4_put_headers("https://x.example/storage/v1/s3/bucket/anchors/1.json", b"{}",
                                       access_key="AK", secret="SK", region="ap-southeast-1", now=now)
    assert h["x-amz-date"] == "20261022T000000Z"
    assert h["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/20261022/ap-southeast-1/s3/aws4_request, "
                                         "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, Signature=")
    other = anchor_store.sigv4_put_headers("https://x.example/storage/v1/s3/bucket/anchors/1.json", b"{ }",
                                           access_key="AK", secret="SK", region="ap-southeast-1", now=now)
    assert other["authorization"] != h["authorization"]


def test_uuid7_is_monotonic_within_a_millisecond():
    ids = [uuid7() for _ in range(5000)]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(i.version == 7 and i.variant == "specified in RFC 4122" for i in ids[:10])
