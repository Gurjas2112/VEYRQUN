# 0014. Public demo: anonymous guest sandboxes, a hard expiry, and a playground on the real path

**Status:** accepted · 2026-10-22

## Context
Reviewers should be able to try the system with **any input**, without an account, for a short window, and
what they see must be exactly what an agent would get. The demo runs on GKE (ADR 0010's gateway + this repo's Helm
chart) with no IdP for visitors.

## Decisions
**Guests.**
- `POST /auth/demo` exists only when `DEMO_MODE=1` and before `DEMO_EXPIRES_AT`.
- Each call creates an anonymous user: no email or name is collected, so there is nothing personal to protect.
- The user gets a private **sandbox workspace** (RLS-isolated like any tenant) whose expiry is capped at the demo
  expiry. The sandbox is seeded with the fleet from `agents/` (fee reversal, card replacement, travel, rogue).
- Guests hold `security_operator`, `app_owner` and `approver` in that sandbox only.
- Rate limits are per IP and global per hour. Cross-site requests are refused with an Origin check (no login CSRF).

**The one exception, kept narrow.**
- Privileged roles normally require MFA and step-up. A guest has no IdP, so `require()` waives both **only** for
  sessions flagged `demo_guest`, and only while the session's workspace is a live sandbox.
- If a guest session points anywhere else, it gets 403. Normal users without MFA are still refused. Tests cover both.

**Hard expiry, enforced on the server.**
- After `DEMO_EXPIRES_AT`, guest login returns 410 and existing guest sessions get 410.
- `/bundles/current` refuses sandbox workspaces. Gateways keep the last good bundle only until
  `BUNDLE_MAX_STALENESS_S`, then **fail closed** for those workspaces through the existing staleness path. Real
  workspaces are unaffected.
- Separately, a Helm CronJob scales the demo to zero at the expiry time (cost guard).

**Playground = the production path.**
- `POST /playground/run` (`actions:simulate`, refused in production workspaces) mints a short-lived token for one of
  the caller's agents and calls the **gateway**. Classification, the injection signal, Rego, the Lua counters,
  approvals, redaction and output scanning are the real code, not a re-implementation, so any input gets a correct
  answer by construction.
- The token carries the `veyrqun:explain` scope, and only then does the gateway add `x-veyrqun-explain`: outcome,
  rule, data classes, signals, amount. That way production agents can't use the header to probe thresholds.
- Every answer includes a plain-English reason mapped from the deterministic `rule_id` (`veyrqun/explain.py`), plus
  the signed ledger entry.

**Audit writer when workers sleep.** `AUDIT_DRAIN_ON_READ=1` makes audit reads drain the decision stream first
(bounded, under the writer's lease). The stream is idempotent on `decision_id`, so it is safe alongside the
standalone writer and lets the playground show the ledger entry immediately.

## Consequences
- `e2e/test_playground_e2e.py` drives a guest through allow, five kinds of block, redaction, approval (single use),
  chat, and output leak blocking, then verifies the ledger.
- `control-plane/tests/test_demo.py` checks isolation between guests, that the waiver stays confined to the sandbox,
  the rate limit, the Origin check and the expiry.
- The guest exception is a deliberate, documented weakening of the MFA rule for synthetic data. It must stay off
  (`DEMO_MODE=0`, the default) on any deployment with real tenants.
