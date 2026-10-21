"""Operator CLI.

  python -m veyrqun.cli admin bootstrap --issuer URL --sub IDP_SUBJECT --email EMAIL [--workspace default]
  python -m veyrqun.cli jobs run {chain_check,reconcile_spend,rebuild_counters,purge_sandboxes}

The first platform_admin of a fresh install is created here, never through a web form.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import jobs, service
from .auth import directory


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veyrqun")
    sub = ap.add_subparsers(dest="group", required=True)

    admin = sub.add_parser("admin").add_subparsers(dest="cmd", required=True)
    boot = admin.add_parser("bootstrap", help="create the first platform_admin (refuses if one exists)")
    boot.add_argument("--issuer", required=True)
    boot.add_argument("--sub", required=True, help="the user's subject (sub) at the IdP")
    boot.add_argument("--email", required=True)
    boot.add_argument("--workspace", default="default")

    job = sub.add_parser("jobs").add_subparsers(dest="cmd", required=True)
    run = job.add_parser("run")
    run.add_argument("name", choices=["chain_check", "reconcile_spend", "rebuild_counters", "purge_sandboxes"])

    a = ap.parse_args(argv)
    service.default_workspace()
    if a.group == "admin":
        try:
            user_id = directory.bootstrap_admin(a.issuer, a.sub, a.email, a.workspace)
        except directory.DirectoryError as exc:
            print(f"error: {exc.detail}", file=sys.stderr)
            return 1
        print(json.dumps({"user_id": str(user_id), "role": "platform_admin", "workspace": a.workspace}))
        return 0
    if a.name == "purge_sandboxes":
        print(json.dumps({"purged": jobs.purge_sandboxes()}))
        return 0
    results = {str(ws): getattr(jobs, a.name)(ws) for ws in jobs.all_workspaces()}
    print(json.dumps(results, indent=2, default=str))
    return 0 if all(not isinstance(r, dict) or r.get("ok", True) for r in results.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
