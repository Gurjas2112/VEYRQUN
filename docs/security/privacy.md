# Personal data inventory

What VEYRQUN stores about people, where, how it is protected, how long it's kept, and who can read it. "People" means
the operators who sign in (admins, operators, approvers, auditors, app owners). The agents' end customers are not
users of this system. Their data is only *seen in transit* by the gateway, and by default it is hashed, not stored.

| Data | Where | Protection | Retention | Readable by |
|---|---|---|---|---|
| Email, display name | `iam.users.email_enc`, `display_enc` | AES-256-GCM under a per-person key, itself wrapped by the master key (envelope) | until erasure | the person (`GET /auth/me/data`), `iam:read` roles |
| Email lookup | `iam.users.email_hash` | keyed HMAC-SHA256 (`VEYRQUN_LOOKUP_KEY`), not reversible | until erasure | nobody (equality checks only) |
| IdP subject | `iam.users.idp_issuer`, `idp_sub` | plain (opaque id from the IdP) | until erasure | platform admins |
| Roles, memberships | `iam.memberships` | row-level security per workspace | until removed | the person, `iam:read` |
| Sessions | Redis `vq:sess:<sha256(id)>` | only a hash of the session id is stored; HttpOnly `__Host-` cookie | idle 15–30 min, absolute 8–12 h | the person, platform admins (revoke) |
| Actions they took | `audit.events.actor = user:<uuid>` | hash-chained, HMAC-signed, anchored; append-only | permanent (it is the audit trail) | `audit:read` in that workspace |
| Prompts and tool payloads (agent traffic) | not stored by default; `request_sha256` / `response_sha256` only | `AUDIT_PAYLOADS=true` opts in per deployment | n/a | n/a |
| Detected data classes | `audit.events.details.data_classes` | class names only (e.g. `pci_pan`), never values | with the ledger | `audit:read` |
| Public-demo guests | no email, no name: an anonymous user and a sandbox | sandbox purged at its expiry | ≤ demo window (5 days) | the guest only |

## Rights and controls
- **Access:** `GET /auth/me/data` returns identity, memberships, sessions and the number of ledger entries the person
  authored (`Cache-Control: no-store`).
- **Erasure:** `POST /admin/users/{id}/erase` deletes the person's key (crypto-shredding). Ledger entries stay
  intact and verifiable, but `user:<uuid>` can no longer be linked to a name or email. All sessions are revoked.
- **Minimisation:** guests carry no identity. Payloads are hashed by default. Detected PII is recorded as a class,
  never as the value. Card numbers are displayed masked (first 6 / last 4 at most).
- **Integrity on retrieval:** each ledger read can be re-verified (`GET /audit/{id}/verify` checks hash and HMAC), and
  the whole chain with anchors (`GET /audit-log/verify`). Constraints in the database reject impossible rows
  (migration 0006).

## Not covered (be explicit)
- Backups and log retention are deployment responsibilities. The Helm chart does not configure Postgres backups.
- The model provider (Vertex AI in the demo) receives prompts after redaction. Its data handling is governed by the
  provider's terms, not by this system.
