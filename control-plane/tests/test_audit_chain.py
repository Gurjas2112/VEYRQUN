from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from veyrqun import audit, service
from veyrqun.db import tx


def _activity(n=20):
    for i in range(n):
        service.attempt_action("travel_agent_01", "flight_rebook" if i % 2 else "credit_limit_increase", Decimal("10"))


def _as_dba(owner_engine, sql, params=None):
    """Simulates an attacker with owner rights who first disables the append-only trigger."""
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit.events DISABLE TRIGGER events_append_only"))
        conn.execute(text(sql), params or {})
        conn.execute(text("ALTER TABLE audit.events ENABLE TRIGGER events_append_only"))


def test_empty_chain_is_valid():
    result = audit.verify_chain()
    assert (result["valid"], result["entries_checked"], result["head_hash"]) == (True, 0, audit.GENESIS)


def test_every_decision_is_logged_and_chain_verifies():
    _activity(20)
    result = audit.verify_chain()
    assert result["valid"] is True and result["entries_checked"] == 20


def test_operator_events_are_chained_too():
    _activity(3)
    service.stop_agent("travel_agent_01")
    service.fleet_stop()
    service.fleet_resume()
    types = [e["action_type"] for e in audit.query(service.default_workspace(), limit=10)]
    assert {"operator:emergency_stop", "operator:fleet_emergency_stop", "operator:fleet_resume"} <= set(types)
    assert audit.verify_chain()["valid"] is True


def test_runtime_role_cannot_update_or_delete_the_ledger():
    _activity(2)
    for sql in ("UPDATE audit.events SET amount = 0", "DELETE FROM audit.events"):
        with pytest.raises(DBAPIError) as exc, tx() as conn:
            conn.execute(text(sql))
        assert "permission denied" in str(exc.value).lower()


def test_trigger_blocks_even_the_owner(owner_engine):
    _activity(1)
    with pytest.raises(DBAPIError) as exc, owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM audit.events"))
    assert "append-only" in str(exc.value)


def test_tampered_amount_is_detected_at_the_exact_entry(owner_engine):
    _activity(10)
    with owner_engine.connect() as conn:
        victim = conn.execute(text("SELECT id FROM audit.events ORDER BY seq OFFSET 4 LIMIT 1")).scalar_one()
    _as_dba(owner_engine, "UPDATE audit.events SET amount = 0.01 WHERE id = :id", {"id": victim})
    result = audit.verify_chain()
    assert result["valid"] is False and result["broken_at"] == str(victim) and result["reason"] == "entry_hash invalid"


def test_deleted_row_is_detected(owner_engine):
    _activity(10)
    with owner_engine.connect() as conn:
        ids = [r.id for r in conn.execute(text("SELECT id FROM audit.events ORDER BY seq"))]
    _as_dba(owner_engine, "DELETE FROM audit.events WHERE id = :id", {"id": ids[3]})
    result = audit.verify_chain()
    assert result["valid"] is False and result["broken_at"] == str(ids[4]) and result["reason"] == "prev_hash mismatch"


def test_reordering_is_detected(owner_engine):
    _activity(6)
    with owner_engine.connect() as conn:
        rows = conn.execute(text("SELECT seq FROM audit.events ORDER BY seq")).all()
    a, b = rows[1].seq, rows[2].seq
    # Swap via a temporary value: rows can share a timestamp, and the (seq, ts) key is checked per row.
    for frm, to in ((a, -1), (b, a), (-1, b)):
        _as_dba(owner_engine, "UPDATE audit.events SET seq = :to WHERE seq = :frm", {"frm": frm, "to": to})
    assert audit.verify_chain()["valid"] is False


def test_rehashed_forgery_is_caught_by_the_hmac(owner_engine):
    """An attacker with DB access recomputes the SHA-256 of a forged row, but can't recompute its HMAC."""
    _activity(6)
    with owner_engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM audit.events ORDER BY seq")).all()
    forged = {**audit.row_to_entry(rows[2]), "outcome": "allow", "rule_id": None}
    forged["entry_hash"] = audit.compute_entry_hash(forged)
    _as_dba(owner_engine, "UPDATE audit.events SET outcome = 'allow', rule_id = NULL, entry_hash = :h WHERE id = :id",
            {"h": forged["entry_hash"], "id": rows[2].id})
    result = audit.verify_chain()
    assert result["valid"] is False and result["broken_at"] == str(rows[2].id) and result["reason"] == "hmac invalid"


def test_single_entry_lookup():
    entry, _, _ = service.attempt_action("travel_agent_01", "flight_rebook", Decimal("5"))
    assert audit.get(entry["id"], service.default_workspace())["entry_hash"] == entry["entry_hash"]
