from veyrqun import audit, service
from veyrqun.db import get_db


def _generate_activity(n=20):
    for i in range(n):
        service.attempt_action("travel_agent_01", "flight_rebook" if i % 2 else "credit_limit_increase", 10.0)


def test_empty_chain_is_valid():
    assert audit.verify_chain() == {"valid": True, "entries_checked": 0, "head_hash": audit.GENESIS}


def test_every_decision_is_logged_and_chain_verifies():
    _generate_activity(20)
    result = audit.verify_chain()
    assert result["valid"] is True
    assert result["entries_checked"] == 20


def test_operator_events_are_chained_too():
    _generate_activity(3)
    service.stop_agent("travel_agent_01")
    service.fleet_stop()
    service.fleet_resume()
    types = [e["action_type"] for e in audit.query_entries(limit=10)]
    assert {"operator:emergency_stop", "operator:fleet_emergency_stop", "operator:fleet_resume"} <= set(types)
    assert audit.verify_chain()["valid"] is True


def test_tampered_amount_is_detected():
    _generate_activity(10)
    conn = get_db()
    victim = conn.execute("SELECT action_id FROM audit_logs ORDER BY rowid LIMIT 1 OFFSET 4").fetchone()["action_id"]
    conn.execute("UPDATE audit_logs SET amount = 0.01 WHERE action_id = ?", (victim,))
    conn.commit()
    conn.close()

    result = audit.verify_chain()
    assert result == {**result, "valid": False, "broken_at": victim, "reason": "entry_hash invalid"}


def test_deleted_row_is_detected():
    _generate_activity(10)
    conn = get_db()
    rows = conn.execute("SELECT action_id FROM audit_logs ORDER BY rowid").fetchall()
    conn.execute("DELETE FROM audit_logs WHERE action_id = ?", (rows[3]["action_id"],))
    conn.commit()
    conn.close()

    result = audit.verify_chain()
    assert result["valid"] is False
    assert result["broken_at"] == rows[4]["action_id"]
    assert result["reason"] == "prev_hash mismatch"


def test_rehashing_a_tampered_row_still_breaks_the_next_link():
    _generate_activity(6)
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM audit_logs ORDER BY rowid")]
    forged = {**rows[2], "outcome": "approved", "rule_triggered": None}
    forged["entry_hash"] = audit.compute_entry_hash(forged)
    conn.execute(
        "UPDATE audit_logs SET outcome = ?, rule_triggered = ?, entry_hash = ? WHERE action_id = ?",
        (forged["outcome"], forged["rule_triggered"], forged["entry_hash"], forged["action_id"]),
    )
    conn.commit()
    conn.close()

    result = audit.verify_chain()
    assert result["valid"] is False
    assert result["broken_at"] == rows[3]["action_id"]
