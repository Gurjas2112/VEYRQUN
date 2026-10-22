# 0012. Signed audit pipeline: one global chain, one writer, HMAC + signed anchors

**Status:** accepted · 2026-10-22

## Context
The gateway decides inline and must not wait on Postgres (latency budget, ADR 0010). It emits each decision to a
Redis Stream (`vq:decisions`) and moves on. Something has to turn those events into the tamper-evident ledger, and
a plain SHA-256 chain only proves *internal consistency*: anyone with write access to the database can rewrite a row
and recompute every later hash, and the chain still verifies.

## Decisions

**One global chain, one active writer.** `python -m veyrqun.audit_writer` consumes the stream as consumer group
`audit-writer`. Only the holder of a Redis lease (`SET NX PX`, renewed by compare-and-extend) consumes, so extra
replicas are warm standbys. Appends also take a Postgres advisory lock, so the API's own audit entries (operator
actions, legacy `/simulate/action`) interleave safely with gateway events.
- *Trade-off accepted:* write throughput is bounded by one serial writer (each append is one short transaction).
  Per-tenant chains would parallelise but multiply anchors and verification work, and make "was anything deleted
  across the platform?" harder to answer. Revisit if the measured backlog grows under the k6 soak test (Phase 7).

**Exactly-once effect over at-least-once delivery.** `audit.decision_ids` (primary key `decision_id`, append-only)
makes `append` idempotent. A redelivered or re-sent event returns the existing entry and is acked. Crashed consumers'
messages are reclaimed with `XAUTOCLAIM` after `AUDIT_CLAIM_IDLE_MS`.

**Nothing is silently dropped.** Schema violations and unknown workspaces go straight to the dead-letter stream
`vq:decisions:dlq` with the reason. Transient failures (DB down) are not acked, so they are retried and go to the
DLQ only after `AUDIT_MAX_DELIVERIES`. `GET /audit/pipeline` reports backlog, pending, DLQ depth and writer heartbeat.

**Hash v2 + HMAC.** Entries now hash `details` too (data classes, redactions, payload hashes, bundle revision,
timings, approval id). Each `entry_hash` is HMAC-SHA256'd under a rotatable secret (`iam.signing_keys`, purpose
`audit_hmac`, envelope-encrypted). Old v1 rows keep verifying with the v1 field set and are reported as
`legacy_entries_without_hmac`. Rotation retires the key but keeps it for verification.

**Signed anchors.** Every `AUDIT_ANCHOR_INTERVAL_S`, the writer signs `{seq, head_hash, entries}` with an Ed25519
`audit_anchor` key (public key in the JWKS), stores it in append-only `audit.anchors`, and optionally copies it to an
S3-compatible bucket (`anchor_store.py`, a minimal SigV4 PUT checked against AWS's published example).
`verify_chain` checks every anchor signature and that the chain still contains each anchored head.

## What each layer catches (each has a test in `test_audit_pipeline.py` / `test_audit_chain.py`)

| Attack | Caught by |
|---|---|
| Edit a row, leave hashes | entry hash |
| Edit a row and recompute its hash | HMAC (attacker lacks the secret) |
| Edit `details` only | hash v2 covers `details` |
| Reorder or delete a middle row | `prev_hash` link |
| Delete the newest rows | anchor (chain shorter than an anchored head) |
| DB owner **and** stolen HMAC key rewrite the tail | anchor head mismatch; the off-database copy if the anchors table is also rewritten |
| Forge an anchor | Ed25519 signature |

## Limits
- Supabase Storage has no S3 Object Lock, so the off-database copy is signed (tamper-evident), not WORM. On AWS, point
  `ANCHOR_S3_*` at a bucket with Object Lock in compliance mode.
- Between the last anchor and the head, an attacker holding both DB owner rights and the HMAC key can still rewrite
  history undetected. The anchor interval bounds that window.
- Offline export verification (`scripts/verify_export.py`) checks hashes and links. The HMAC needs the secret, so it is
  verified server-side (`GET /audit/{id}/verify`, `GET /audit-log/verify`).
