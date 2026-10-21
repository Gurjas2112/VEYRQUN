# 0002. Policy as code: one compiled Rego artifact, evaluated in-process everywhere

**Status:** accepted · 2026-10-22

## Context
Decisions must be deterministic, testable, and identical in the TypeScript gateway (data plane) and the Python
control plane (legacy `/simulate/action`, dry runs). A network hop to an OPA sidecar adds latency and a failure mode
to every request; two hand-written engines would drift.

## Decision
- Rules live in `policies/veyrqun/authz.rego` (ordered, first match wins, same `rule_id`s as v2). Per-agent limits are
  bundle **data** (`data.policy_data`), not code.
- `opa build -t wasm` compiles one artifact, `policies/dist/policy.wasm`. It is committed, and CI fails if it is stale
  (the build is deterministic).
- The gateway evaluates it with `@open-policy-agent/opa-wasm`. The control plane uses a small wasmtime host
  (`veyrqun/policy/opa_wasm.py`, ABI 1.2 `opa_eval` fast path), so both run the identical bytes.
- The Python engine from v2 is kept only as an **oracle** (`veyrqun/policy/oracle.py`). `scripts/policy_parity.py`
  checks Rego + Lua against it on 10,000 randomized, boundary-heavy cases in CI. A deliberate `>` to `>=` mutation in
  the per-transaction rule produced 617 mismatches, so the check catches off-by-one bugs.
- The policy uses no host-only builtins. The host refuses to load a policy that requires any (fail fast).

## Consequences
- No sidecar and no network hop for decisions. Wasm instances are per thread and per data revision in Python.
- The Python host costs about 0.1 ms per evaluation (wasmtime call overhead). It serves only the control-plane path;
  the gateway latency budget is measured separately (Phase 2).
- Changing Rego means rebuilding the wasm (`python scripts/build_policy.py`), and CI enforces it.

## Alternatives considered
- OPA server sidecar: simplest, but adds a hop and one more thing to keep alive per pod.
- Cedar: a good fit for authorization, but it has no WASM path shared with the existing Python oracle work.
