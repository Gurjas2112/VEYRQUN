"""Migrate a VEYRQUN v2 SQLite database into the v3 PostgreSQL schema.

  python scripts/migrate_sqlite_to_pg.py legacy.db [--workspace legacy-v2] [--pg-url URL] [--allow-broken-chain]

Extract -> transform -> load in ONE Postgres transaction (all or nothing):
  * REAL money -> NUMERIC(18,2) via exact Decimal(repr(float)); values that aren't exactly representable at
    2 dp are counted as precision anomalies and rounded half-even (reported, never silent).
  * JSON-in-TEXT lists/maps -> text[] / jsonb; text ids -> UUIDv7 with a migration.id_map row each.
  * v2 audit history is copied verbatim into audit.legacy_v2_events (its own hash fields), after the v2 chain
    is verified at the source. The v2 head hash is then anchored into the v3 chain as an operator event,
    so v2 history is covered by v3's tamper evidence without being re-hashed.
  * v2 "spend_used_today" is *not* carried over: v2 "daily" meant "since last manual reset"; v3 counters
    roll over at local midnight. The report lists the values for reconciliation.
Runs as the schema owner (MIGRATION_DATABASE_URL). Writes a run report into migration.runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from veyrqun import audit  # noqa: E402
from veyrqun.db import insert_policy_version  # noqa: E402
from veyrqun.ids import uuid7  # noqa: E402

CENT = Decimal("0.01")
V2_HASHED = ("action_id", "agent_id", "action_type", "amount", "timestamp", "outcome", "rule_triggered",
             "policy_version", "prev_hash")


class MigrationError(RuntimeError):
    pass


@dataclass
class Report:
    precision_anomalies: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    v2_chain: dict = field(default_factory=dict)
    spend_not_migrated: dict = field(default_factory=dict)


def money(value: float | None, where: str, report: Report) -> Decimal | None:
    if value is None:
        return None
    exact = Decimal(repr(float(value)))
    rounded = exact.quantize(CENT, rounding=ROUND_HALF_EVEN)
    if exact != rounded:
        report.precision_anomalies.append({"field": where, "source": repr(value), "migrated": str(rounded)})
    if rounded < 0:
        raise MigrationError(f"negative money at {where}: {value!r}")
    return rounded


def v2_entry_hash(row: sqlite3.Row) -> str:
    """The v2 algorithm, verbatim (json.dumps default separators, float amount)."""
    payload = json.dumps({k: row[k] for k in V2_HASHED}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_v2_chain(rows: list) -> dict:
    expected = "GENESIS"
    for i, row in enumerate(rows):
        if row["prev_hash"] != expected:
            return {"valid": False, "broken_at": row["action_id"], "index": i, "reason": "prev_hash mismatch"}
        if v2_entry_hash(row) != row["entry_hash"]:
            return {"valid": False, "broken_at": row["action_id"], "index": i, "reason": "entry_hash invalid"}
        expected = row["entry_hash"]
    return {"valid": True, "entries": len(rows), "head_hash": expected}


def _policy_from_v2(row: sqlite3.Row, where: str, report: Report) -> dict:
    limits = json.loads(row["action_limits"] or "{}")
    return {
        "allowed_actions": json.loads(row["allowed_actions"]),
        "blocked_actions": json.loads(row["blocked_actions"]),
        "spend_cap_daily": money(row["spend_cap_daily"], f"{where}.spend_cap_daily", report),
        "max_per_transaction": money(row["max_per_transaction"], f"{where}.max_per_transaction", report),
        "rate_limit_per_minute": row["rate_limit_per_minute"],
        "action_limits": {k: money(v, f"{where}.action_limits.{k}", report) for k, v in limits.items()},
    }


def migrate(sqlite_path: Path, pg_url: str, workspace_slug: str = "legacy-v2", allow_broken_chain: bool = False) -> dict:
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    report = Report()
    try:
        agents = src.execute("SELECT * FROM agents ORDER BY rowid").fetchall()
        versions = src.execute("SELECT * FROM policy_versions ORDER BY agent_id, version").fetchall()
        events = src.execute("SELECT rowid AS legacy_rowid, * FROM audit_logs ORDER BY rowid").fetchall()
        fleet = dict(src.execute("SELECT key, value FROM fleet_state").fetchall())
    finally:
        src.close()

    report.v2_chain = verify_v2_chain(events)
    if not report.v2_chain["valid"] and not allow_broken_chain:
        raise MigrationError(f"v2 audit chain is broken at the source: {report.v2_chain}; refusing to migrate")

    run_id = uuid7()
    engine = create_engine(pg_url, poolclass=NullPool)
    with engine.begin() as conn:
        if conn.execute(text("SELECT 1 FROM iam.workspaces WHERE slug = :s"), {"s": workspace_slug}).first():
            raise MigrationError(f"workspace {workspace_slug!r} already exists; migrations are not re-runnable into it")
        conn.execute(text("INSERT INTO migration.runs (run_id, source) VALUES (:r, :s)"),
                     {"r": run_id, "s": str(sqlite_path.name)})
        ws = uuid7()
        conn.execute(text("INSERT INTO iam.workspaces (id, slug, name) VALUES (:id, :slug, :name)"),
                     {"id": ws, "slug": workspace_slug, "name": f"Migrated from v2 ({sqlite_path.name})"})
        conn.execute(text("INSERT INTO gov.fleet_state (workspace_id, stopped) VALUES (:w, :s)"),
                     {"w": ws, "s": fleet.get("fleet_stopped") == "1"})

        by_agent: dict[str, list] = {}
        for v in versions:
            by_agent.setdefault(v["agent_id"], []).append(v)

        for a in agents:
            legacy = a["agent_id"]
            new_id = uuid7()
            conn.execute(text("INSERT INTO migration.id_map VALUES (:r, 'agent', :l, :n)"),
                         {"r": run_id, "l": legacy, "n": new_id})
            conn.execute(
                text("INSERT INTO gov.agents (id, workspace_id, agent_key, display_name, status, status_reason, "
                     "policy_version, business_service) VALUES (:id, :ws, :key, :name, :status, :reason, :pv, 'migrated')"),
                {"id": new_id, "ws": ws, "key": legacy, "name": a["display_name"], "status": a["status"],
                 "reason": a["status_reason"], "pv": a["policy_version"]},
            )
            history = by_agent.get(legacy, [])
            if a["policy_version"] not in {v["version"] for v in history}:
                raise MigrationError(f"agent {legacy}: current policy v{a['policy_version']} missing from history")
            for v in history:
                policy = _policy_from_v2(v, f"policy_versions[{legacy}/v{v['version']}]", report)
                insert_policy_version(conn, new_id, v["version"], policy, "system:migration", v["note"] or "migrated")
                conn.execute(text("INSERT INTO migration.id_map VALUES (:r, 'policy_version', :l, "
                                  "(SELECT id FROM gov.policy_versions WHERE agent_id = :a AND version = :v))"),
                             {"r": run_id, "l": v["policy_id"], "a": new_id, "v": v["version"]})
            report.spend_not_migrated[legacy] = str(money(a["spend_used_today"], f"agents[{legacy}].spend_used_today", report))

        for e in events:
            conn.execute(
                text("INSERT INTO audit.legacy_v2_events (legacy_rowid, action_id, agent_id, action_type, amount_raw, "
                     "amount, legacy_ts, outcome, rule_triggered, policy_version, prev_hash, entry_hash, run_id) "
                     "VALUES (:rowid, :aid, :agent, :act, :raw, :amt, :ts, :out, :rule, :pv, :prev, :hash, :run)"),
                {"rowid": e["legacy_rowid"], "aid": e["action_id"], "agent": e["agent_id"], "act": e["action_type"],
                 "raw": e["amount"], "amt": money(e["amount"], f"audit[{e['action_id']}].amount", report),
                 "ts": e["timestamp"], "out": e["outcome"], "rule": e["rule_triggered"], "pv": e["policy_version"],
                 "prev": e["prev_hash"], "hash": e["entry_hash"], "run": run_id},
            )

        anchor = audit.append(
            conn, workspace_id=ws, actor=f"system:migration:{run_id}", agent_id="*fleet*",
            action_type="operator:legacy_import", amount=Decimal("0"), currency="USD", outcome="executed",
            rule_id=None, policy_version=0, decision_id=f"v2-head:{report.v2_chain.get('head_hash', 'BROKEN')}",
        )
        conn.execute(text("INSERT INTO gov.bundle_revisions (reason) VALUES (:r)"), {"r": f"migration {run_id}"})

        report.counts = {"agents": len(agents), "policy_versions": len(versions), "audit_events": len(events)}
        summary = {
            "run_id": str(run_id),
            "workspace": workspace_slug,
            "source": sqlite_path.name,
            "counts": report.counts,
            "v2_chain": report.v2_chain,
            "anchor_entry_id": anchor["id"],
            "precision_anomalies": len(report.precision_anomalies),
            "precision_anomaly_samples": report.precision_anomalies[:10],
            "spend_not_migrated": report.spend_not_migrated,
        }
        conn.execute(text("UPDATE migration.runs SET finished_at = now(), report = CAST(:rep AS jsonb) WHERE run_id = :r"),
                     {"rep": json.dumps(summary), "r": run_id})
    engine.dispose()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite", type=Path)
    ap.add_argument("--workspace", default="legacy-v2")
    ap.add_argument("--pg-url", default=os.environ.get("MIGRATION_DATABASE_URL"))
    ap.add_argument("--allow-broken-chain", action="store_true")
    args = ap.parse_args()
    if not args.pg_url:
        sys.exit("set --pg-url or MIGRATION_DATABASE_URL (schema owner)")
    print(json.dumps(migrate(args.sqlite, args.pg_url, args.workspace, args.allow_broken_chain), indent=2))


if __name__ == "__main__":
    main()
