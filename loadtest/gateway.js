// k6 load test for the VEYRQUN gateway. Overhead = gateway time excluding the upstream call, taken from
// the gateway's own Server-Timing header (`gateway;dur=`), so network and upstream latency don't pollute it.
//   k6 run -e GATEWAY=http://host:8091 -e TOKEN=<agent jwt> -e RATE=200 -e DURATION=30s loadtest/gateway.js
import http from "k6/http";
import { check } from "k6";
import { Trend, Counter } from "k6/metrics";

const overhead = new Trend("gateway_overhead_ms", true);
const upstream = new Trend("upstream_ms", true);
const decisions = new Counter("decisions");

const RATE = Number(__ENV.RATE || 200);
export const options = {
  scenarios: {
    mcp_tool_calls: { executor: "constant-arrival-rate", rate: RATE, timeUnit: "1s", duration: __ENV.DURATION || "30s",
      preAllocatedVUs: 50, maxVUs: 200, exec: "toolCall" },
    llm_chat: { executor: "constant-arrival-rate", rate: Math.max(1, Math.floor(RATE / 4)), timeUnit: "1s",
      duration: __ENV.DURATION || "30s", preAllocatedVUs: 20, maxVUs: 100, exec: "chat" },
  },
  thresholds: { "gateway_overhead_ms": ["p(99)<10"], "checks": ["rate>0.99"] },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max", "count"],
};

const headers = { "content-type": "application/json", authorization: `Bearer ${__ENV.TOKEN}` };

function record(res) {
  const timing = res.headers["Server-Timing"] || "";
  const g = /gateway;dur=([\d.]+)/.exec(timing);
  const u = /upstream;dur=([\d.]+)/.exec(timing);
  if (g) overhead.add(Number(g[1]));
  if (u) upstream.add(Number(u[1]));
  decisions.add(1);
}

export function toolCall() {
  const res = http.post(`${__ENV.GATEWAY}/mcp`, JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call",
    params: { name: "waive_fee", arguments: { amount: "0.01", account: "SYNTH-001" } } }), { headers });
  check(res, { "tool call allowed": (r) => r.status === 200 });
  record(res);
}

export function chat() {
  const res = http.post(`${__ENV.GATEWAY}/v1/chat/completions`, JSON.stringify({ model: "mock-model",
    messages: [{ role: "user", content: "Summarise the refund policy for account SYNTH-001." }] }), { headers });
  check(res, { "chat allowed": (r) => r.status === 200 });
  record(res);
}
