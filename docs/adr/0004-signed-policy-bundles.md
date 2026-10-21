# 0004. Signed, deterministic policy bundles with ETag

**Status:** accepted · 2026-10-22

## Context
On the hosted demo, gateways pull policy from the control plane over public HTTPS, so the bundle must be authenticated
end to end (not only by TLS) and cheap to poll.

## Decision
- `GET /bundles/current?workspace=` returns an OPA-layout tar.gz (`policy.wasm`, `data.json`, `.manifest`) that is
  byte-for-byte deterministic (mtime 0, sorted JSON). The ETag is the SHA-256 of the content, and `If-None-Match`
  returns 304.
- The bundle is signed with Ed25519 (`X-Veyrqun-Signature`, `X-Veyrqun-Signature-Kid`). The public key is in the same
  JWKS as the agent-token keys. Gateways verify before hot-swapping and keep the last good bundle until
  `BUNDLE_MAX_STALENESS_S` runs out.
- A bearer service token is required when `BUNDLE_SERVICE_TOKEN` is set, which is mandatory in production.
- Signing keys are stored encrypted (AES-256-GCM envelope, `kid` per key) and rotate without breaking in-flight
  verification (retired keys stay in JWKS for 24 hours).

## Alternatives considered
OPA's native `.signatures.json` (JWS) format. It was not chosen because the WASM SDK does not verify it, so the
gateway would need custom verification anyway. A detached header signature is simpler to verify in both languages.
