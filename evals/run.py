"""Guardrail eval harness: replay the labelled corpus through the real gateway and report how each guardrail does.

  docker compose up -d postgres redis && npm --prefix gateway ci
  python -m evals.run                          # local stack (mock model), writes docs/results/guardrail_eval.json
  python -m evals.run --base-url https://host  # a deployed demo (real model), --no-write to just print
  python -m evals.run --gate                   # CI: exit 1 if a must-block case passes or benign FPR rises

Every request goes through POST /playground/run, which mints an agent token and calls the gateway, so this measures
the production path (classification, injection signal, OPA policy, Redis limits, output scanning), not a copy of it.
Misses are listed by id. Nothing is tuned to the corpus after the fact without the corpus changing in git.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from evals.cases import cases

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "results" / "guardrail_eval.json"
BASELINE = ROOT / "evals" / "baseline.json"


@contextlib.contextmanager
def local_target():
    sys.path.insert(0, str(ROOT / "e2e"))
    from stack import local_stack

    origin = "http://evals.local"
    env = {"DEMO_MODE": "1", "DEMO_EXPIRES_AT": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
           "SESSION_COOKIE_SECURE": "false", "AUDIT_DRAIN_ON_READ": "1", "ALLOWED_ORIGINS": origin,
           "DEMO_GUESTS_PER_IP_PER_HOUR": "100"}
    with local_stack(name="evals", db="veyrqun_evals", redis_db=7, cp_port=8018, gw_port=8098, llm_port=9261,
                     mcp_port=9262, service_token="evals-token", extra_env=env) as st:  # noqa: S106 (throwaway stack)
        yield st.cp, origin


def guest(api: str, origin: str) -> httpx.Client:
    c = httpx.Client(base_url=api, timeout=60, headers={"Origin": origin})
    r = c.post("/auth/demo")
    r.raise_for_status()
    csrf = next(v for k, v in c.cookies.items() if k.endswith("veyrqun_csrf"))
    c.headers["X-CSRF-Token"] = csrf
    return c


def judge(case: dict, r: dict) -> tuple[bool, str]:
    outcome, rule = r.get("outcome"), r.get("rule_id")
    classes = (r.get("detail") or {}).get("data_classes") or []
    went_through = outcome in ("allow", "redact")
    if case["expect"] == "block":
        return (not went_through), f"{outcome}/{rule}"
    if case["expect"] == "redact":
        return (went_through and case["cls"] in classes and outcome == "redact"), f"{outcome}/{classes}"
    return went_through, f"{outcome}/{rule}"


def run(api: str, origin: str) -> dict:
    client = guest(api, origin)
    per_case, latencies = [], []
    for i, case in enumerate(cases()):
        if i and i % 25 == 0:  # each guest sandbox has a daily request quota; spread the corpus over a few
            client = guest(api, origin)
        body = {k: case[k] for k in ("agent_id", "kind", "amount", "text")}
        if case["kind"] == "tool":
            body["action"] = case["action"]
        t0 = time.perf_counter()
        resp = client.post("/playground/run", json=body)
        latencies.append((time.perf_counter() - t0) * 1000)
        r = resp.json() if resp.status_code == 200 else {"outcome": f"http_{resp.status_code}", "rule_id": None}
        ok, got = judge(case, r)
        per_case.append({"id": case["id"], "category": case["category"], "expect": case["expect"], "ok": ok,
                         "got": got, "caught_by": r.get("rule_id")})
    by_cat: dict[str, dict] = defaultdict(lambda: {"cases": 0, "correct": 0, "misses": []})
    for c in per_case:
        b = by_cat[c["category"]]
        b["cases"] += 1
        b["correct"] += c["ok"]
        if not c["ok"]:
            b["misses"].append(f"{c['id']} ({c['got']})")
    for b in by_cat.values():
        b["rate"] = round(b["correct"] / b["cases"], 3)
    attacks = [c for c in per_case if c["expect"] == "block"]
    benign = [c for c in per_case if c["category"].startswith("benign")]
    return {
        "generated_at": datetime.now(UTC).isoformat(), "target": api, "cases": len(per_case),
        "must_block": {"cases": len(attacks), "stopped": sum(c["ok"] for c in attacks),
                       "detection_rate": round(sum(c["ok"] for c in attacks) / len(attacks), 3)},
        "benign": {"cases": len(benign), "false_positives": sum(not c["ok"] for c in benign),
                   "false_positive_rate": round(sum(not c["ok"] for c in benign) / len(benign), 3)},
        "by_category": dict(sorted(by_cat.items())),
        "round_trip_ms": {"median": round(statistics.median(latencies), 1),
                          "p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
                          "note": "client -> control plane -> gateway -> upstream (mock or real) -> back"},
        "per_case": per_case,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", help="deployed demo, e.g. https://136.82.10.126.nip.io")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--gate", action="store_true", help="fail on regressions against evals/baseline.json")
    ap.add_argument("--save-baseline", action="store_true")
    a = ap.parse_args()
    if a.base_url:
        result = run(a.base_url.rstrip("/") + "/api", a.base_url.rstrip("/"))
    else:
        with local_target() as (api, origin):
            result = run(api, origin)
    summary = {k: result[k] for k in ("cases", "must_block", "benign", "round_trip_ms")}
    print(json.dumps(summary, indent=2))
    for cat, b in result["by_category"].items():
        print(f"{cat:20s} {b['correct']:3d}/{b['cases']:<3d} {b['rate']:.0%}  {'; '.join(b['misses'])[:200]}")
    if not a.no_write and not a.base_url:
        RESULTS.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if a.save_baseline:
        BASELINE.write_text(json.dumps({"must_block_stopped": sorted(c["id"] for c in result["per_case"]
                                                                       if c["expect"] == "block" and c["ok"]),
                                        "benign_false_positive_rate": result["benign"]["false_positive_rate"]},
                                       indent=2) + "\n")
    if a.gate:
        base = json.loads(BASELINE.read_text())
        now_stopped = {c["id"] for c in result["per_case"] if c["expect"] == "block" and c["ok"]}
        regressed = sorted(set(base["must_block_stopped"]) - now_stopped)
        fpr_up = result["benign"]["false_positive_rate"] > base["benign_false_positive_rate"]
        if regressed or fpr_up:
            print("GATE FAILED:", {"no_longer_stopped": regressed, "benign_fpr_increased": fpr_up})
            sys.exit(1)
        print("gate passed")


if __name__ == "__main__":
    main()
