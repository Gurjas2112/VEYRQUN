# 0011. Gateway semantics: action names, spend, streaming scans, kill-switch ordering

**Status:** accepted · 2026-10-22

## Decisions

**Action names.** LLM calls are `llm.chat`. MCP `tools/call` uses the **tool name itself** (`waive_fee`,
`flight_rebook`, …) rather than the spec's suggested `tool:<name>`, so one policy governs a capability whether it
arrives via MCP, the legacy API or a future channel. Tool amounts come from a per-tool JSON pointer in the bundle
(`settings.tool_amounts`). `tools/list` is filtered to the tools the agent may call.

**LLM spend.** It is estimated *before* the call (prompt ≈ chars/4, plus `max_tokens`, priced from `MODEL_PRICES`) so
caps apply up front, then corrected from `usage` with `adjust.lua`. Costs are rounded **up** to a whole minor unit.
Unknown models are refused (they can't be metered).

**Streaming.** The scanner holds back the last `STREAM_SCAN_HOLDBACK` characters (default 64, longer than any
detected identifier) and never releases text that could still be part of a match. So a card number split across SSE
chunks is caught before any of it leaves the gateway. A blocked data class stops the stream with a final
`event: error`. A redacted class is replaced inline. Detectors are checksum-validated (Luhn, Verhoeff, IBAN mod-97)
to keep false positives down.

**Retries.** LLM calls are treated as idempotent (no side effects) and fail over across `UPSTREAM_LLM_URLS` before
the first response byte. Tool calls are **never retried**. On a timeout the spend reservation is kept, because the
tool may have run, and the reconciliation job settles it against the ledger.

**Kill switch.** The control plane publishes `{type: fleet, stopped, version}` and also writes `vq:fleet:{ws}`.
Gateways apply only **strictly newer versions**. Integration tests found that accepting equal or older versions let a
delayed "resume" un-stop a fleet. A 1 s poll of the key backs up a lost pub/sub message. Measured end to end
(operator action → first refused request): median 315 ms, max 351 ms (`docs/results/kill_switch.json`).

**Approvals.** On `require_approval` the gateway creates the pending approval in the control plane (internal API,
service token) and returns `202`. The agent polls `GET /v1/approvals/{id}` and retries with `x-veyrqun-approval`.
The approval is bound to agent, action and amount, and is consumed once.
