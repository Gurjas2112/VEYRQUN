"""Parity check: compiled Rego (policy.wasm) + Redis Lua counters vs the Python oracle.

Usage: python scripts/policy_parity.py [cases] [--seed N]   (needs Redis; uses REDIS_URL)
Writes docs/results/policy_parity.json. The pytest suite calls run() with 10,000 cases.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from veyrqun.config import get_settings  # noqa: E402
from veyrqun.policy.opa_wasm import OpaWasmPolicy  # noqa: E402
from veyrqun.policy.oracle import evaluate as oracle  # noqa: E402

ACTIONS = ["pay", "refund", "waive_fee", "wire_transfer", "card_issue", "open_account"]
CLASSES = ["email", "phone", "pci_pan", "aadhaar", "iban"]


def _maybe(rng, value, p_none=0.3):
    return None if rng.random() < p_none else value


def random_agent(rng: random.Random) -> dict:
    allowed = rng.sample(ACTIONS, rng.randint(0, 5))
    remaining = [a for a in ACTIONS if a not in allowed] or ACTIONS
    blocked = rng.sample(remaining, rng.randint(0, min(2, len(remaining))))
    if rng.random() < 0.1:  # occasionally overlapping lists, to pin "deny wins"
        blocked = list(set(blocked) | set(allowed[:1]))
    cap = rng.choice([0, 1, 100, 5_000, 25_000, 100_000])
    return {
        "status": rng.choices(["active", "stopped", "revoked"], [0.9, 0.05, 0.05])[0],
        "mode": rng.choice(["enforce", "observe"]),
        "policy_version": rng.randint(1, 20),
        "allowed_actions": allowed,
        "blocked_actions": blocked,
        "spend_cap_daily_minor": cap,
        "max_per_transaction_minor": _maybe(rng, rng.choice([0, 50, 1_000, cap])),
        "action_limits_minor": {a: rng.choice([0, 10, 500, 2_000]) for a in rng.sample(ACTIONS, rng.randint(0, 2))},
        "action_daily_caps_minor": {a: rng.choice([0, 100, 3_000]) for a in rng.sample(ACTIONS, rng.randint(0, 2))},
        "rate_limit_per_minute": _maybe(rng, rng.choice([0, 1, 2, 5]), 0.4),
        "approval_threshold_minor": _maybe(rng, rng.choice([0, 100, 1_000, 10_000]), 0.6),
        "redact_data_classes": rng.sample(CLASSES, rng.randint(0, 2)),
        "block_data_classes": rng.sample(CLASSES, rng.randint(0, 1)),
    }


def random_case(rng: random.Random, agent_ids: list[str], agents: dict) -> dict:
    agent_id = rng.choice(agent_ids + ["ghost"])
    agent = agents.get(agent_id)
    # Amounts cluster on the limits (limit-1, limit, limit+1), where off-by-one bugs live.
    anchors = [0, 1, 99, 100, 101, 1_000, 5_000]
    if agent:
        for v in (agent["spend_cap_daily_minor"], agent["max_per_transaction_minor"], agent["approval_threshold_minor"]):
            if v is not None:
                anchors += [max(0, v - 1), v, v + 1]
    signal = rng.choice([None, "missing", "missing", "missing", 0.1, 0.89, 0.9, 0.95])
    case = {
        "input": {
            "agent_id": agent_id,
            # Mostly actions the agent may perform, so counter/approval/redaction paths get exercised.
            "action": rng.choice(agent["allowed_actions"]) if agent and agent["allowed_actions"] and rng.random() < 0.75
            else rng.choice(ACTIONS),
            "amount_minor": rng.choice(anchors),
            "data_classes": sorted(rng.sample(CLASSES, rng.choice([0, 0, 1, 2]))),
            "approval_granted": rng.random() < 0.2,
            "fleet_state": {"stopped": rng.random() < 0.02},
        },
        "spend": rng.choice([0, 0, 50, 99_900, 100_000]),
        "action_spend": rng.choice([0, 2_950, 3_000]),
        "recent": rng.choice([0, 0, 1, 2, 5]),
    }
    if signal != "missing":
        case["input"]["signals"] = {"injection_score": signal}
    return case


def _redis_full_decision(r, scripts, decision: dict, case: dict, n: int) -> tuple[str, str | None]:
    if decision["outcome"] not in ("allow", "redact"):
        return decision["outcome"], decision["rule_id"]
    reserve, _ = scripts
    tag = f"{{parity:{n}}}"
    keys = [f"vq:{tag}:spend", f"vq:{tag}:aspend", f"vq:{tag}:rate"]
    now = int(time.time() * 1000)
    pipe = r.pipeline()
    pipe.delete(*keys)
    pipe.set(keys[0], case["spend"])
    pipe.set(keys[1], case["action_spend"])
    for i in range(case["recent"]):
        pipe.zadd(keys[2], {f"r{i}": now - 1_000})
    pipe.execute()
    lim = decision["limits"]

    def v(x):
        return -1 if x is None else x

    ok, rule, _ = reserve(keys=keys, args=[case["input"]["amount_minor"], v(lim.get("spend_cap_minor")),
                                           v(lim.get("action_daily_cap_minor")), v(lim.get("rate_limit_per_minute")),
                                           now, 60_000, f"res-{n}", 60])
    r.delete(*keys)
    if not ok:
        return "block", rule
    return decision["outcome"], decision["rule_id"]


def run(cases: int = 10_000, seed: int = 20261022) -> dict:
    import redis

    settings = get_settings()
    rng = random.Random(seed)
    agents = {f"agent_{i}": random_agent(rng) for i in range(400)}
    data = {"policy_data": {"agents": agents, "fleet": {"stopped": False}, "settings": {"injection_block_threshold": 0.9}}}
    policy = OpaWasmPolicy(settings.policy_wasm_path.read_bytes())
    policy.set_data(data)
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    scripts = (r.register_script((settings.lua_dir / "reserve.lua").read_text()),
               r.register_script((settings.lua_dir / "release.lua").read_text()))
    ids = list(agents)
    mismatches = []
    by_outcome: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    started = time.perf_counter()
    for n in range(cases):
        case = random_case(rng, ids, agents)
        inp = case["input"]
        decision = policy.evaluate(inp)
        got = _redis_full_decision(r, scripts, decision, case, n)
        exp_outcome, exp_rule, exp_red = oracle(
            agents.get(inp["agent_id"]), inp, spend_minor=case["spend"], action_spend_minor=case["action_spend"],
            recent_actions=case["recent"], injection_threshold=0.9)
        by_outcome[got[0]] = by_outcome.get(got[0], 0) + 1
        by_rule[str(got[1])] = by_rule.get(str(got[1]), 0) + 1
        redactions_match = got[0] != "redact" or decision["redactions"] == exp_red
        if got != (exp_outcome, exp_rule) or not redactions_match:
            mismatches.append({"case": case, "agent": agents.get(inp["agent_id"]), "rego": got,
                               "oracle": [exp_outcome, exp_rule]})
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "cases": cases,
        "mismatches": len(mismatches),
        "first_mismatches": mismatches[:5],
        "outcome_distribution": dict(sorted(by_outcome.items())),
        "rule_distribution": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "seconds": round(time.perf_counter() - started, 2),
        "method": "policy.wasm (Python wasmtime host) + reserve.lua (real Redis) vs Python oracle",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="?", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=20261022)
    args = ap.parse_args()
    result = run(args.cases, args.seed)
    out = ROOT / "docs" / "results" / "policy_parity.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "first_mismatches"}, indent=2))
    sys.exit(1 if result["mismatches"] else 0)
