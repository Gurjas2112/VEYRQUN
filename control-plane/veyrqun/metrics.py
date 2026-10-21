"""In-process enforcement metrics: decision counts and latency percentiles.

Exposed as JSON for the dashboard and in Prometheus text format for scraping.
"""

import threading
import time
from collections import Counter, defaultdict, deque


def _percentile(sorted_values: list[int], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct / 100.0 * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _summary_us(samples) -> dict:
    values = sorted(samples)
    return {
        "samples": len(values),
        "p50_us": round(_percentile(values, 50) / 1000, 2),
        "p95_us": round(_percentile(values, 95) / 1000, 2),
        "p99_us": round(_percentile(values, 99) / 1000, 2),
        "max_us": round((values[-1] if values else 0) / 1000, 2),
    }


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.started_at = time.time()
            self.outcomes: Counter = Counter()
            self.rules: Counter = Counter()
            self.per_agent: dict[str, Counter] = defaultdict(Counter)
            self.operator_events: Counter = Counter()
            # Pure policy-engine time vs. full decision time (lock + DB + audit append).
            self.eval_ns: deque = deque(maxlen=10_000)
            self.decision_ns: deque = deque(maxlen=10_000)

    def observe_decision(self, agent_id: str, outcome: str, rule: str | None, eval_ns: int, decision_ns: int) -> None:
        with self._lock:
            self.outcomes[outcome] += 1
            if rule:
                self.rules[rule] += 1
            self.per_agent[agent_id][outcome] += 1
            self.eval_ns.append(eval_ns)
            self.decision_ns.append(decision_ns)

    def observe_operator_event(self, event: str) -> None:
        with self._lock:
            self.operator_events[event] += 1

    def snapshot(self) -> dict:
        with self._lock:
            total = sum(self.outcomes.values())
            # "approved" = the action went ahead (allow, or allow with redaction).
            approved = self.outcomes.get("allow", 0) + self.outcomes.get("redact", 0)
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "decisions_total": total,
                "approved_total": approved,
                "blocked_total": self.outcomes.get("block", 0),
                "pending_approval_total": self.outcomes.get("require_approval", 0),
                "outcomes": dict(self.outcomes),
                "approval_rate": round(approved / total, 4) if total else None,
                "blocked_by_rule": dict(self.rules),
                "per_agent": {agent: dict(c) for agent, c in self.per_agent.items()},
                "operator_events": dict(self.operator_events),
                "policy_eval_latency": _summary_us(self.eval_ns),
                "decision_latency": _summary_us(self.decision_ns),
            }

    def prometheus(self, fleet: dict) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP veyrqun_decisions_total Policy decisions by outcome.",
            "# TYPE veyrqun_decisions_total counter",
            f'veyrqun_decisions_total{{outcome="approved"}} {snap["approved_total"]}',
            f'veyrqun_decisions_total{{outcome="blocked"}} {snap["blocked_total"]}',
            "# HELP veyrqun_blocked_total Blocked decisions by triggering rule.",
            "# TYPE veyrqun_blocked_total counter",
        ]
        lines += [f'veyrqun_blocked_total{{rule="{rule}"}} {n}' for rule, n in snap["blocked_by_rule"].items()]
        lines += [
            "# HELP veyrqun_agent_decisions_total Policy decisions per agent.",
            "# TYPE veyrqun_agent_decisions_total counter",
        ]
        for agent, counts in snap["per_agent"].items():
            for outcome, n in counts.items():
                lines.append(f'veyrqun_agent_decisions_total{{agent_id="{agent}",outcome="{outcome}"}} {n}')
        lines += [
            "# HELP veyrqun_operator_events_total Control-plane operator actions.",
            "# TYPE veyrqun_operator_events_total counter",
        ]
        lines += [f'veyrqun_operator_events_total{{event="{e}"}} {n}' for e, n in snap["operator_events"].items()]
        for name, key in (("policy_eval", "policy_eval_latency"), ("decision", "decision_latency")):
            lines += [
                f"# HELP veyrqun_{name}_latency_microseconds {name.replace('_', ' ')} latency quantiles.",
                f"# TYPE veyrqun_{name}_latency_microseconds summary",
            ]
            for q, field in (("0.5", "p50_us"), ("0.95", "p95_us"), ("0.99", "p99_us")):
                lines.append(f'veyrqun_{name}_latency_microseconds{{quantile="{q}"}} {snap[key][field]}')
            lines.append(f"veyrqun_{name}_latency_microseconds_count {snap[key]['samples']}")
        lines += [
            "# HELP veyrqun_fleet_stopped 1 while the fleet-wide emergency stop is engaged.",
            "# TYPE veyrqun_fleet_stopped gauge",
            f"veyrqun_fleet_stopped {1 if fleet['fleet_stopped'] else 0}",
            "# HELP veyrqun_agents Agents by status.",
            "# TYPE veyrqun_agents gauge",
        ]
        lines += [f'veyrqun_agents{{status="{s}"}} {n}' for s, n in fleet["agents_by_status"].items()]
        return "\n".join(lines) + "\n"


metrics = Metrics()
