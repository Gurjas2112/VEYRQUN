import { Counter, Gauge, Histogram, Registry, collectDefaultMetrics } from "prom-client";

export class Metrics {
  readonly registry = new Registry();
  readonly decisions = new Counter({
    name: "veyrqun_gateway_decisions_total", help: "Gateway decisions",
    labelNames: ["outcome", "rule", "action_kind", "enforced"], registers: [this.registry],
  });
  readonly overhead = new Histogram({
    name: "veyrqun_gateway_overhead_seconds", help: "Time spent in the gateway, excluding the upstream call",
    labelNames: ["route"], buckets: [0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25], registers: [this.registry],
  });
  readonly upstream = new Histogram({
    name: "veyrqun_gateway_upstream_seconds", help: "Upstream (LLM/MCP) latency", labelNames: ["route", "status"],
    buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30], registers: [this.registry],
  });
  readonly streamAborts = new Counter({
    name: "veyrqun_gateway_stream_aborts_total", help: "Streams stopped for policy violations", labelNames: ["data_class"],
    registers: [this.registry],
  });
  readonly redisErrors = new Counter({ name: "veyrqun_gateway_redis_errors_total", help: "Counter store errors", registers: [this.registry] });
  readonly bundleAge = new Gauge({
    name: "veyrqun_gateway_bundle_age_seconds", help: "Seconds since the last successful bundle sync", labelNames: ["workspace"],
    registers: [this.registry],
  });
  readonly killSwitch = new Gauge({
    name: "veyrqun_gateway_kill_switch", help: "1 while the fleet kill switch is active", labelNames: ["workspace"],
    registers: [this.registry],
  });
  readonly auditBacklog = new Gauge({ name: "veyrqun_gateway_audit_backlog", help: "Undelivered decision events", registers: [this.registry] });

  constructor() {
    collectDefaultMetrics({ register: this.registry });
  }
}
