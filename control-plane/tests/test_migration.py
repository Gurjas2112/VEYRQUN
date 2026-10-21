"""Barclays-style schema conversion + automated data validation, end to end on a real v2 database."""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from conftest import OWNER_URL

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_sqlite_to_pg as mig  # noqa: E402
import validate_migration as val  # noqa: E402


@pytest.fixture(scope="module")
def v2_db(tmp_path_factory):
    out = tmp_path_factory.mktemp("legacy") / "v2.db"
    # Separate process: the fixture builder imports the *v2* `veyrqun` package from git history.
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_v2_fixture.py"), str(out), "--actions", "150"],
                   check=True, capture_output=True)
    return out


def test_migration_round_trip_passes_every_validation_check(v2_db):
    summary = mig.migrate(v2_db, OWNER_URL)
    assert summary["v2_chain"]["valid"] and summary["counts"]["audit_events"] >= 150
    result = val.validate(v2_db, OWNER_URL)
    failed = [c for c in result["checks"] if not c["passed"]]
    assert result["passed"], failed
    assert result["checks_total"] >= 15


def test_migration_is_not_rerunnable_into_the_same_workspace(v2_db):
    mig.migrate(v2_db, OWNER_URL)
    with pytest.raises(mig.MigrationError, match="already exists"):
        mig.migrate(v2_db, OWNER_URL)


def test_broken_source_chain_is_refused_and_nothing_is_written(v2_db, tmp_path, owner_engine):
    bad = tmp_path / "tampered.db"
    shutil.copy(v2_db, bad)
    with sqlite3.connect(bad) as c:
        c.execute("UPDATE audit_logs SET amount = amount + 1 WHERE rowid = 10")
    with pytest.raises(mig.MigrationError, match="broken at the source"):
        mig.migrate(bad, OWNER_URL)
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM iam.workspaces WHERE slug = 'legacy-v2'")).scalar() == 0


def test_precision_anomalies_are_reported_not_silently_rounded(v2_db, tmp_path):
    odd = tmp_path / "odd.db"
    shutil.copy(v2_db, odd)
    with sqlite3.connect(odd) as c:
        c.execute("UPDATE agents SET spend_used_today = 10.005 WHERE agent_id = 'travel_agent_01'")
    summary = mig.migrate(odd, OWNER_URL)
    assert summary["precision_anomalies"] == 1
    assert summary["precision_anomaly_samples"][0]["source"] == "10.005"


def test_validator_catches_post_migration_tampering(v2_db, owner_engine):
    mig.migrate(v2_db, OWNER_URL)
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit.legacy_v2_events DISABLE TRIGGER legacy_append_only"))
        conn.execute(text("UPDATE audit.legacy_v2_events SET amount = amount + 0.01 "
                          "WHERE legacy_rowid = (SELECT min(legacy_rowid) FROM audit.legacy_v2_events WHERE amount > 0)"))
        conn.execute(text("UPDATE gov.policy_versions SET spend_cap_daily = spend_cap_daily + 1 "
                          "WHERE id = (SELECT min(id::text)::uuid FROM gov.policy_versions WHERE created_by = 'system:migration')"))
        conn.execute(text("ALTER TABLE audit.legacy_v2_events ENABLE TRIGGER legacy_append_only"))
    result = val.validate(v2_db, OWNER_URL)
    failed = {c["check"] for c in result["checks"] if not c["passed"]}
    assert not result["passed"]
    assert {"audit.amount_total_exact", "policy_versions.equal"} <= failed
