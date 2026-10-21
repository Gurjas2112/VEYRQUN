"""Build a realistic legacy v2 SQLite database by running the *actual* v2 code from git history.

Usage: python scripts/make_v2_fixture.py OUT.db [--actions N] [--rev 85ab965]
The v2 package is extracted with `git archive` into a temp dir; nothing in the working tree changes.
"""

from __future__ import annotations

import argparse
import io
import os
import random
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2_REV = "85ab965"  # "Rebrand to VEYRQUN and complete the financial-agent governance layer" (v2.0.0)


def extract_v2(rev: str, dest: Path) -> Path:
    blob = subprocess.run(["git", "-C", str(ROOT), "archive", rev, "backend/veyrqun"], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "backend"


def build(out: Path, actions: int, rev: str, seed: int = 7) -> None:
    out = out.resolve()
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory() as tmp:
        backend = extract_v2(rev, Path(tmp))
        os.environ["VEYRQUN_DB_FILE"] = str(out)
        sys.path.insert(0, str(backend))
        for name in [m for m in sys.modules if m == "veyrqun" or m.startswith("veyrqun.")]:
            del sys.modules[name]
        from veyrqun import db as v2db  # the legacy package
        from veyrqun import service as v2svc

        v2db.init_db()
        rng = random.Random(seed)
        pool = {
            "travel_agent_01": [("flight_rebook", 450.0), ("hotel_rebook", 300.0), ("credit_limit_increase", 1000.0)],
            "fee_reversal_02": [("waive_fee", 35.0), ("account_summary", 0.0), ("wire_transfer", 250.0)],
            "credit_limit_03": [("credit_limit_increase", 2000.0), ("score_check", 0.0)],
            "card_replacement_04": [("card_issue", 25.0), ("card_lock", 0.0), ("waive_fee", 50.0)],
            "balance_check_05": [("account_summary", 0.0), ("balance_check", 0.0), ("wire_transfer", 500.0)],
        }
        for i in range(actions):
            agent = rng.choice(list(pool))
            action, base = rng.choice(pool[agent])
            amount = round(base * rng.uniform(0.8, 1.5), 2) if base else 0.0
            v2svc.attempt_action(agent, action, amount)
            if i == actions // 3:
                v2svc.update_policy("fee_reversal_02", {"spend_cap_daily": 750.0}, note="legacy cap raise")
                v2svc.stop_agent("credit_limit_03", "legacy incident")
            if i == actions // 2:
                v2svc.revoke_agent("card_replacement_04", "legacy compromise")
                v2svc.resume_agent("credit_limit_03")
        sys.path.remove(str(backend))
        for name in [m for m in sys.modules if m == "veyrqun" or m.startswith("veyrqun.")]:
            del sys.modules[name]
        # v2 pools connections per thread and its close() only rolls back; really close them so the
        # file isn't held open (Windows) once the fixture is built.
        for conn in list(getattr(v2db._local, "pool", {}).values()):
            sqlite3.Connection.close(conn)
    print(f"wrote legacy v2 fixture {out} ({actions} simulated actions)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--actions", type=int, default=300)
    ap.add_argument("--rev", default=V2_REV)
    args = ap.parse_args()
    build(args.out, args.actions, args.rev)
