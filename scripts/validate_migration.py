"""Independent post-migration validation (does not trust the migrator's own report).

  python scripts/validate_migration.py legacy.db [--workspace legacy-v2] [--pg-url URL] [--out docs/results/migration_validation.json]

Re-reads the SQLite source and the Postgres target and checks:
  row counts · id-map completeness · per-agent field equality · policy-version equality · exact money totals ·
  outcome distribution · v2 chain recomputed *from the target copy* · v3 anchor present and v3 chain valid ·
  domain/null/range sweep. Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "control-plane"))

from migrate_sqlite_to_pg import verify_v2_chain  # noqa: E402

CENT = Decimal("0.01")


def _dec(x) -> Decimal | None:
    return None if x is None else Decimal(repr(float(x))).quantize(CENT, rounding=ROUND_HALF_EVEN)


def validate(sqlite_path: Path, pg_url: str, workspace_slug: str = "legacy-v2") -> dict:
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    s_agents = {r["agent_id"]: r for r in src.execute("SELECT * FROM agents")}
    s_versions = {(r["agent_id"], r["version"]): r for r in src.execute("SELECT * FROM policy_versions")}
    s_events = src.execute("SELECT * FROM audit_logs ORDER BY rowid").fetchall()
    src.close()

    checks: list[dict] = []

    def check(name: str, passed: bool, **details) -> None:
        checks.append({"check": name, "passed": bool(passed), **details})

    engine = create_engine(pg_url, poolclass=NullPool)
    with engine.connect() as conn:
        ws = conn.execute(text("SELECT id FROM iam.workspaces WHERE slug = :s"), {"s": workspace_slug}).scalar()
        if ws is None:
            raise SystemExit(f"workspace {workspace_slug!r} not found in target")
        t_agents = {r.agent_key: r for r in conn.execute(text("SELECT * FROM gov.agents WHERE workspace_id = :w"), {"w": ws})}
        t_versions = {(r.agent_key, r.version): r for r in conn.execute(text(
            "SELECT p.*, a.agent_key FROM gov.policy_versions p JOIN gov.agents a ON a.id = p.agent_id "
            "WHERE a.workspace_id = :w"), {"w": ws})}
        run = conn.execute(text("SELECT r.* FROM migration.runs r JOIN migration.id_map m ON m.run_id = r.run_id "
                                "JOIN gov.agents a ON a.id = m.new_id WHERE a.workspace_id = :w LIMIT 1"), {"w": ws}).first()
        t_events = conn.execute(text("SELECT * FROM audit.legacy_v2_events WHERE run_id = :r ORDER BY legacy_rowid"),
                                {"r": run.run_id}).all()
        id_map = conn.execute(text("SELECT entity, legacy_id, new_id FROM migration.id_map WHERE run_id = :r"),
                              {"r": run.run_id}).all()
        anchor = conn.execute(text("SELECT decision_id FROM audit.events WHERE workspace_id = :w "
                                   "AND action_type = 'operator:legacy_import'"), {"w": ws}).scalar()

    # 1. row counts
    check("row_count.agents", len(s_agents) == len(t_agents), source=len(s_agents), target=len(t_agents))
    check("row_count.policy_versions", len(s_versions) == len(t_versions), source=len(s_versions), target=len(t_versions))
    check("row_count.audit_events", len(s_events) == len(t_events), source=len(s_events), target=len(t_events))

    # 2. id map: every legacy id mapped exactly once, every target agent reachable from the map
    mapped = Counter((m.entity, m.legacy_id) for m in id_map)
    agent_map = {m.legacy_id: m.new_id for m in id_map if m.entity == "agent"}
    check("id_map.agents_complete", set(agent_map) == set(s_agents) and all(c == 1 for c in mapped.values()),
          missing=sorted(set(s_agents) - set(agent_map)))
    check("id_map.targets_exist", all(t_agents[k].id == v for k, v in agent_map.items() if k in t_agents))

    # 3. per-agent fields
    agent_diffs = []
    for key, s in s_agents.items():
        t = t_agents.get(key)
        if t is None:
            agent_diffs.append({"agent": key, "issue": "missing"})
            continue
        for fld in ("display_name", "status", "status_reason", "policy_version"):
            if s[fld] != getattr(t, fld):
                agent_diffs.append({"agent": key, "field": fld, "source": s[fld], "target": getattr(t, fld)})
    check("agents.fields_equal", not agent_diffs, diffs=agent_diffs[:10])

    # 4. policy versions: sets and exact money
    pol_diffs = []
    for (agent, ver), s in s_versions.items():
        t = t_versions.get((agent, ver))
        if t is None:
            pol_diffs.append({"agent": agent, "version": ver, "issue": "missing"})
            continue
        pairs = {
            "allowed_actions": (set(json.loads(s["allowed_actions"])), set(t.allowed_actions)),
            "blocked_actions": (set(json.loads(s["blocked_actions"])), set(t.blocked_actions)),
            "spend_cap_daily": (_dec(s["spend_cap_daily"]), t.spend_cap_daily),
            "max_per_transaction": (_dec(s["max_per_transaction"]), t.max_per_transaction),
            "rate_limit_per_minute": (s["rate_limit_per_minute"], t.rate_limit_per_minute),
            "action_limits": ({k: _dec(v) for k, v in json.loads(s["action_limits"] or "{}").items()},
                              {k: Decimal(str(v)) for k, v in (t.action_limits or {}).items()}),
        }
        for fld, (a, b) in pairs.items():
            if a != b:
                pol_diffs.append({"agent": agent, "version": ver, "field": fld, "source": str(a), "target": str(b)})
    check("policy_versions.equal", not pol_diffs, diffs=pol_diffs[:10])

    # 5. money totals (exact) and outcome distribution
    s_total = sum((_dec(e["amount"]) for e in s_events), Decimal("0"))
    t_total = sum((e.amount for e in t_events), Decimal("0"))
    check("audit.amount_total_exact", s_total == t_total, source=str(s_total), target=str(t_total))
    s_out, t_out = Counter(e["outcome"] for e in s_events), Counter(e.outcome for e in t_events)
    check("audit.outcome_distribution", s_out == t_out, source=dict(s_out), target=dict(t_out))
    raw_equal = all(float(s["amount"]) == t.amount_raw for s, t in zip(s_events, t_events, strict=False))
    check("audit.raw_amounts_preserved", raw_equal and len(s_events) == len(t_events))

    # 6. v2 chain recomputed from the TARGET copy must match the source head
    as_rows = [{"action_id": e.action_id, "agent_id": e.agent_id, "action_type": e.action_type, "amount": e.amount_raw,
                "timestamp": e.legacy_ts, "outcome": e.outcome, "rule_triggered": e.rule_triggered,
                "policy_version": e.policy_version, "prev_hash": e.prev_hash, "entry_hash": e.entry_hash} for e in t_events]
    target_chain = verify_v2_chain(as_rows)
    source_chain = verify_v2_chain(s_events)
    check("legacy_chain.valid_in_target", target_chain["valid"], **{k: v for k, v in target_chain.items() if k != "valid"})
    check("legacy_chain.head_matches_source", target_chain.get("head_hash") == source_chain.get("head_hash"))

    # 7. anchor in the v3 chain + whole v3 chain valid
    check("v3_anchor.present", anchor == f"v2-head:{source_chain.get('head_hash')}", anchor=anchor)
    os.environ.setdefault("DATABASE_URL", pg_url)
    from veyrqun import audit as v3audit

    v3 = v3audit.verify_chain()
    check("v3_chain.valid", v3["valid"], entries=v3.get("entries_checked"))

    # 8. domain / null / range sweep on the target
    bad = []
    for key, t in t_agents.items():
        if t.status not in ("active", "stopped", "revoked"):
            bad.append({"agent": key, "status": t.status})
    for (agent, ver), t in t_versions.items():
        if t.spend_cap_daily is None or t.spend_cap_daily < 0:
            bad.append({"agent": agent, "version": ver, "spend_cap_daily": str(t.spend_cap_daily)})
    for e in t_events:
        if e.amount < 0 or e.outcome not in ("approved", "blocked", "executed") or not e.entry_hash:
            bad.append({"event": e.action_id})
    check("domain_sweep", not bad, violations=bad[:10])

    passed = all(c["passed"] for c in checks)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": sqlite_path.name,
        "workspace": workspace_slug,
        "run_id": str(run.run_id),
        "passed": passed,
        "checks_total": len(checks),
        "checks_failed": sum(not c["passed"] for c in checks),
        "precision_anomalies_reported_by_migrator": (run.report or {}).get("precision_anomalies"),
        "checks": checks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite", type=Path)
    ap.add_argument("--workspace", default="legacy-v2")
    ap.add_argument("--pg-url", default=os.environ.get("MIGRATION_DATABASE_URL"))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = validate(args.sqlite, args.pg_url, args.workspace)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
    for c in result["checks"]:
        print(("PASS " if c["passed"] else "FAIL ") + c["check"])
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
