# 0010. Enforce inline in a gateway, not in an agent SDK

**Status:** accepted · 2026-10-22

## Context
v2 only governed agents that *chose* to call `/simulate/action`. An agent that called its LLM or a bank API
directly was invisible. Governance that relies on the governed party's cooperation isn't a control.

## Decision
- All model and tool traffic goes through the **gateway** (Node 22 + TypeScript, Fastify): OpenAI-compatible
  `POST /v1/chat/completions` and MCP (`POST /mcp`, JSON-RPC over Streamable HTTP). Agents hold no provider keys,
  and on Kubernetes only the gateway may send egress (NetworkPolicy, Phase 5).
- The pipeline per request: JWT authentication → bundle (fail closed when stale) → data classification → in-process
  OPA WASM decision → Redis Lua reservation → proxy (streaming with scanning) → decision event on a Redis Stream.
- **Language choice:** TypeScript for the data plane, for its streaming/HTTP ecosystem (undici, Fastify) and the
  official OPA WASM SDK. The policy artifact is shared with the Python control plane (ADR 0002), so the languages
  can't drift.
- **Latency budget:** gateway overhead p99 < 10 ms on one host. Measured with k6 at 250 req/s against local mocks:
  **p50 1.0 ms, p99 1.86 ms** (`docs/results/gateway_latency.json`, taken from the gateway's own `Server-Timing`).

## Consequences
- The gateway is on the critical path, so it is stateless and horizontally scalable (state is in Redis; policy in
  memory), with liveness/readiness separated (`/healthz` vs `/readyz`).
- Every failure mode has a defined status code (failure contract §4), and each one has an integration test.

## Alternatives considered
- **Agent SDK / middleware:** easy to adopt, but bypassable and per-language.
- **Service-mesh filter (Envoy ext_authz):** strong placement, but it can't see MCP semantics, estimate token spend,
  or redact inside streamed responses without a custom filter anyway.
