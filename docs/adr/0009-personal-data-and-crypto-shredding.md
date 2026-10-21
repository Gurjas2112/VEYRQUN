# 0009. Personal data: encrypted identities, pseudonymous audit, erasure by crypto-shredding

**Status:** accepted · 2026-10-22

## Context
The audit ledger must be immutable, but people have a right to erasure (DPDP Act, GDPR). Deleting audit rows would
break the hash chain; keeping plaintext identities would block erasure.

## Decision
- The ledger only records pseudonymous actors (`user:<uuid>`, `agent:<key>`).
- Emails and names live in `iam.users`, encrypted with AES-256-GCM under a **per-user identity key**, which is itself
  wrapped by the master key ring (`kid`-tagged, rotatable). Lookup uses a keyed HMAC of the normalized email.
- **Erasure** (`POST /admin/users/{id}/erase`) destroys the identity key, clears the ciphertexts, drops memberships and
  sessions, and audits the act. The chain stays valid while the person can no longer be identified, and the same IdP
  subject can't silently log back in.
- Invite emails are encrypted at rest. Signing keys (agent JWT, bundle) are stored encrypted.
- The append-only ledger is enforced by grants (the runtime role has INSERT/SELECT only on `audit.*`) and by a trigger
  that rejects UPDATE/DELETE even for the owner unless deliberately disabled.

## Consequences
The master key comes from `VEYRQUN_MASTER_KEY` (or a KMS adapter in production; the env var is required when
`VEYRQUN_ENV=production`). Losing the master key makes encrypted identities unreadable by design, so back it up
separately from database backups.
