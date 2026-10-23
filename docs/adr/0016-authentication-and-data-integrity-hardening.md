# 0016. Authentication and data-integrity hardening

**Status:** accepted · 2026-10-23

## Context
After the public deployment, a review against ASVS V2/V3/V5/V8 and property-based testing turned up gaps. None
was exploitable in the demo's configuration, but each would matter in a bank.

## Decisions
- **Token endpoint brute force.** `/oauth/token` counts failures per client id (10) and per IP (50) in a 15-minute
  window. Over the limit it returns 429 with `Retry-After` *before* checking the secret, so guessing gets no signal.
  If Redis is down, this check fails open: a valid secret is still required.
- **Session fixation.** When a session's privileges change (workspace switch, sandbox creation), it gets a new id
  and CSRF token and the old id is destroyed (`sessions.rotate`). Login always creates a fresh session.
- **Storage that can't be confused.** Migration 0006 adds CHECK constraints on the ledger: amount ≥ 0, ISO 4217
  currency, known outcome and mode, known hash version, hex hashes. Even an owner with the append-only trigger
  disabled can't store a row the verifier couldn't trust.
- **No unwritable entries.** Hypothesis found that a NUL character in any text field made `audit.append` fail. A
  crafted input could therefore keep a decision out of the ledger: the writer would dead-letter it. NUL is now
  replaced with U+FFFD before hashing.
- **Property-based tests** (`test_properties.py`) cover money round trips through minor units and canonical
  strings, rejection (never rounding) of excess precision, and store → read → re-hash identity for arbitrary unicode.
- **Data-subject access** via `GET /auth/me/data`, plus a data inventory in `docs/security/privacy.md`.

## Not done (tracked)
- Rotating an agent's secret doesn't revoke tokens already issued, which live up to `AGENT_TOKEN_TTL_S` (15 min).
  The fix is a per-agent `tokens_valid_after` in the bundle, checked by the gateway.
- Revoking or stopping an agent already takes effect immediately (bundle and kill switch), so the exposure is limited
  to the secret-rotation case.
