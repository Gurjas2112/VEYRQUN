# VEYRQUN v3: IDE Agent Prompt

> **How to use this file**
> 1. Copy it into the repo as `docs/AGENT_PROMPT.md`. For a short always-on version, put §0–§3 into
>    [`.github/copilot-instructions.md`](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot) (GitHub Copilot)
>    or [`AGENTS.md`](https://agents.md/) (read by Cursor, Claude Code and other agents).
> 2. Run **one phase at a time** in agent mode. Paste the phase number, for example *"Do Phase 2 from docs/AGENT_PROMPT.md"*.
> 3. Review the diff, run the tests yourself, and commit before starting the next phase.
> 4. Order: 0 → 1 → **1B** → 2 → 3 → 4 → 5 → 6 → 7 (incl. **7B** dashboards) → 8 → 9 → 10.

---

## 0. Role and goal

You are a senior backend and platform engineer working in the **VEYRQUN** repository
([github.com/Gurjas2112/VEYRQUN](https://github.com/Gurjas2112/VEYRQUN)).

VEYRQUN is currently a Python/FastAPI **policy decision API** for financial AI agents. Its features are per-agent
allow/block lists, spend caps, rate limits, revoke and emergency stop, policy versioning with rollback,
and a SHA-256 hash-chained audit log, with a React/Vite dashboard and 45 passing pytest tests.

Turn it into **VEYRQUN v3, a zero-trust governance gateway for AI agent traffic**:
- an **inline TypeScript gateway** that every LLM call and MCP tool call goes through
- a **Python control plane**
- a **signed audit pipeline**
- a **LangGraph demo fleet**
- **governed RAG**
- a **Helm chart**
- a hosted demo with the backend on **Render** and the dashboard on **Vercel**, with Kubernetes as the reference deployment

The project has to stand up to review for two kinds of roles: an AI/infrastructure developer in a bank (Python, OPA,
controls, incident management, test automation), and a founding engineer at an AI-governance startup
(Node/TypeScript, Kubernetes/Helm, HTTP/TLS/streaming, latency budgets, failure modes, on-call).

---

## 1. Non-negotiable rules

1. **Attribution stays.** This project started from [Aegis by infyier](https://github.com/infyier/aegis-governance) (MIT).
   Keep infyier's copyright line in `LICENSE` and the README credit (Phase 0). Never remove or obscure them.
2. **Decisions are deterministic.** Allow, block, redact and require_approval decisions come only from the policy
   engine (OPA/Rego plus counters). **No LLM or ML model may make or override a decision.** Models may only
   emit *signals* (for example `injection_score`) that a Rego rule can read, or draft content that a human approves.
3. **Default-deny and fail-closed.** Unknown agent, unknown tool, missing policy, unreachable state store, or a
   stale policy bundle all lead to a block (§4 lists the exact behaviours).
4. **No made-up numbers.** Every metric in the README or docs must come from a script in this repo, written to
   `docs/results/*.json` and quoted from there. If something hasn't been measured, write `TBD`.
5. **Existing behaviour is preserved.** All 45 existing tests keep passing, adapted only where storage changes.
   Every new feature ships with tests.
6. **Small, reviewable changes.** Use one commit per logical step with [Conventional Commits](https://www.conventionalcommits.org/).
   Record non-obvious decisions as an [ADR](https://adr.github.io/) in `docs/adr/NNNN-title.md`.
7. **No secrets in git.** Configuration comes from env vars, documented in `.env.example` files.
8. **Ask, don't guess.** If a requirement conflicts with the existing code or this document, stop and ask.
9. **Users are governance users, not consumers.** The people who log in are platform admins, security operators,
   approvers, auditors and app owners (the teams whose agents are governed). Do **not** build cardmember or
   consumer accounts. Public self-signup creates only an isolated **demo sandbox** (Phase 1B).
10. **Authorisation is enforced on the server** for every endpoint, against the role matrix in Phase 1B. Hiding a button
    in the UI is not access control.
11. **Money is never a float.** Use `NUMERIC(18,2)` in Postgres and `Decimal` in Python, or integer minor units
    plus an ISO 4217 currency code. The current code stores amounts as `REAL`, which must be fixed in Phase 1.

---

## 2. Current state (read these before changing anything)

| Path | What it is |
|---|---|
| `backend/veyrqun/policy_engine.py` | Pure `evaluate_policy()`: ordered rules, first match wins |
| `backend/veyrqun/service.py` | `attempt_action()`: evaluate, update spend, append audit, all under one `RLock` |
| `backend/veyrqun/audit.py` | SHA-256 hash chain, `verify_chain()` |
| `backend/veyrqun/db.py` | SQLite schema, seed agents, per-thread connection pool |
| `backend/veyrqun/routes/*` | `/simulate/action`, agents, control (stop/revoke/resume), audit, metrics, `/ws/live-feed` |
| `backend/tests/*` | 45 tests: accuracy, precedence, concurrency, tamper detection, latency |
| `frontend/src/*` | React + Vite dashboard (JSX) |
| `docs/Technical Debt.md` | Open issues #1–#7. This plan closes all of them |

Known limits to remove: SQLite plus a single global lock, in-memory rate windows, no auth, no operator identity in
the audit log, hand-coded rules, and agents having to call the API voluntarily (nothing sits inline).

---

## 3. Target architecture

```
                         ┌───────────────── control plane (Python/FastAPI) ─────────────────┐
                         │ agents · policies (Rego bundles) · tokens/JWKS · operator RBAC   │
                         │ approvals queue · audit query/verify · copilot (optional)        │
                         └───────▲───────────────┬──────────────────────────▲───────────────┘
                     bundles(ETag)│      kill/revoke (pub/sub)               │ reads
                                  │               ▼                          │
 LangGraph agents ──HTTPS+JWT──► [ gateway (Node 22 + TypeScript) ] ──► LLM providers (OpenAI-compatible)
 (and any client)                │ authn → classify → OPA(WASM) → caps(Redis Lua) → proxy/stream │──► MCP servers (mock bank core, docs search)
                                 └──── decision events ──► Redis Streams ──► [ audit-writer ] ──► Postgres
                                                                              hash chain + HMAC, head anchoring
 dashboard (React, Vercel) ──/api rewrite──► control plane API + SSE live feed (Render)
 Postgres (+pgvector) and Redis: Neon + Upstash in the Render demo; Helm values on Kubernetes
```

**Target layout**
```
gateway/            # NEW  Node 22, TypeScript, Fastify
control-plane/      # RENAMED from backend/ (keep the python package name `veyrqun`)
audit-writer/       # NEW  Python worker (or a module inside control-plane, with its own entrypoint)
agents/             # NEW  LangGraph demo fleet + mock MCP servers
policies/           # NEW  Rego policies + opa tests + data schema
rag/                # NEW  ingestion, retrieval eval
ml/                 # NEW  Colab notebooks (Unsloth copilot, guard classifier), eval scripts
deploy/helm/veyrqun # NEW  Helm chart
render.yaml         # NEW  Render Blueprint (gateway, control plane, optional worker/cron/idp)
frontend/vercel.json  # existing; add /api rewrites to the control plane + security headers
loadtest/           # NEW  k6 scripts
deploy/observability/  # NEW  Grafana dashboards, alert rules, OTel collector config
deploy/keycloak/       # NEW  realm export (roles, clients, MFA, password policy), no secrets
docs/adr/ docs/runbooks/ docs/results/ docs/security/ docs/COMPLIANCE.md
frontend/           # existing; split into /admin and /app route trees
```

---

## 4. Failure-mode contract (implement it, and test every row)

| Condition | Gateway behaviour | HTTP status |
|---|---|---|
| Missing, invalid or expired agent JWT | Block | `401` |
| Policy denies the action | Block, return `rule_id` + `decision_id` | `403` |
| Rate or spend limit exceeded | Block | `429` with a `Retry-After` header for rate limits |
| `require_approval` | Hold; return `approval_id`; the agent polls or resumes later | `202` |
| Redis unreachable | **Fail closed** for anything that carries spend or rate state; read-only `allow` only if the policy marks it `fail_open_safe` | `503` |
| Control plane unreachable | Keep using the last good bundle until `BUNDLE_MAX_STALENESS_S` (default 300) runs out, then fail closed | `503` after expiry |
| Fleet kill switch active | Block everything within `KILL_PROPAGATION_TARGET_MS` | `403` `fleet_emergency_stop_active` |
| Upstream timeout | Abort, record it, never retry non-idempotent tool calls | `504` |
| Response violates policy mid-stream | Stop the SSE stream, send a final error event, record it | already `200`; error event in the stream |
| Audit queue backlog > threshold | `AUDIT_MODE=strict`: block. `AUDIT_MODE=async`: allow + raise an alert | `503` / `—` |

---

## Phase 0: Attribution and hygiene (do this first)

- [ ] `LICENSE`: keep the MIT text, with **both** copyright lines:
      `Copyright (c) 2026 infyier` and `Copyright (c) 2026 Gurjas Singh Gandhi`.
- [ ] README, near the top: *"Started from [Aegis](https://github.com/infyier/aegis-governance) by infyier
      (American Express CodeStreet 2026). v2 refactor and v3 gateway and platform work by Gurjas Singh Gandhi."*
- [ ] Add a `CHANGELOG.md` that lists what v2 changed relative to Aegis (taken from `docs/Technical Debt.md`
      "Resolved in v2") and what v3 adds.
- [ ] Add `CONTRIBUTING.md` (setup, test commands, commit style, ADR process) and `.editorconfig`.
- [ ] Add GitHub Actions CI ([docs](https://docs.github.com/en/actions)): pytest, ruff, frontend lint and build.

**Done when:** CI is green, and the LICENSE and README credit are present.

---

## Phase 1: Control plane on Postgres, Redis, auth and OPA

1. **Storage:** move to PostgreSQL with [SQLAlchemy 2](https://docs.sqlalchemy.org/en/20/) +
   [psycopg 3](https://www.psycopg.org/psycopg3/docs/) and [Alembic](https://alembic.sqlalchemy.org/) migrations.
   Keep a SQLite option only for fast unit tests. Integration tests run against Postgres (a docker compose service in CI).
   Migrate every money column from `REAL` to `NUMERIC(18,2)` with a currency code (rule 11). IDs are UUIDv7; timestamps are UTC `timestamptz`.
2. **Counters move to Redis** ([Lua scripts](https://redis.io/docs/latest/develop/interact/programmability/eval-intro/)):
   daily spend, per-action caps, and sliding-window rate limits, each as one atomic script that checks and
   increments. Redis is the source of truth for counters. A periodic job snapshots them to Postgres, and
   a scheduled daily rollover runs in each agent's timezone (Tech Debt #3, #4).
   Remove the global `RLock`. Re-run the "50 parallel × $100 vs $1,500 cap → exactly 15 approved" test against Redis.
3. **Policies as code:** port `policy_engine.RULES` to [Rego](https://www.openpolicyagent.org/docs/latest/policy-language/)
   in `policies/veyrqun/authz.rego`, with the same rule order and the same `rule_id` strings.
   Per-agent limits become bundle **data**, not code. Add `require_approval` and `redact` outcomes.
   - Write [`opa test`](https://www.openpolicyagent.org/docs/latest/policy-testing/) cases for every rule and every boundary.
   - Add `scripts/gen_policy_tests.py`, which generates boundary test cases (limit−0.01, limit, limit+0.01) from the
     data schema. This is the automated test generation feature.
   - **Parity test:** the old Python `evaluate_policy` and the new Rego must agree on 10k random inputs.
     Keep the Python engine only as a test oracle.
4. **Bundles:** the control plane serves a versioned [OPA bundle](https://www.openpolicyagent.org/docs/latest/management-bundles/)
   (Rego compiled to WASM + data.json) at `GET /bundles/current`, with `ETag`. Every policy edit or rollback creates a new version.
5. **Auth (Tech Debt #1, #6):**
   - **Humans:** login, signup, roles, sessions and MFA are specified in **Phase 1B**. Do Phase 1B immediately after
     Phase 1, before the gateway.
   - **Agents:** agent registration issues a short-lived **Ed25519-signed agent JWT** (`sub=agent_id`, `env`, `scopes`),
     published at `/.well-known/jwks.json`. Agents never hold provider API keys.
   - Add `actor` (operator or agent identity) to the audit hashed fields.
6. **Approvals API:** `POST /approvals/{id}/approve|reject`, restricted to the `approver` role and audited.
7. Keep `/simulate/action` as a legacy path that goes through the same Rego + Redis logic.

**Done when:** the old tests pass (adapted), the new tests pass, `opa test policies/ -v` passes, and the parity test passes.

---

## Phase 1B: Identity, roles, data protection and validation

### 1B.1 Identity provider and login flow
- **IdP:** [Keycloak](https://www.keycloak.org/documentation), an open-source OIDC provider. Use it in docker compose, as an optional Helm
  subchart (in the free Render demo, a hosted OIDC provider takes its place; see Phase 6). It provides MFA (TOTP and
  [WebAuthn/passkeys](https://www.keycloak.org/docs/latest/server_admin/#webauthn_server_administration_guide)),
  email verification, brute-force detection and password policy. The control plane stays **provider-agnostic OIDC**,
  so a customer can plug in Entra ID, Okta or Google Workspace by changing only issuer, client and secret config.
- **Flow:** a backend-for-frontend in the control plane using the [Authorization Code + PKCE](https://oauth.net/2/pkce/) flow.
  Tokens never reach the browser. The BFF stores the session in Redis and sets the cookie
  `__Host-veyrqun_session` with `HttpOnly; Secure; SameSite=Lax; Path=/`.
  State-changing requests need a CSRF token (double-submit) and must pass an `Origin` check.
- **Same origin:** the dashboard reaches the API through a Vercel `/api/*` rewrite (or one custom domain; see Phase 6), so cookies stay first-party.
  If you use WebSockets, the browser fetches a single-use 30-second ticket over REST and passes it on connect.
- **Token validation (all services):** check `iss`, `aud`, `exp`, `nbf` and the signature via JWKS by `kid`;
  allowlist `RS256`/`EdDSA` and reject `none` or HS* algorithms from external issuers.
- **Sessions:** admin roles get a 15-minute idle and 8-hour absolute timeout; other roles get 30 minutes idle and 12 hours absolute.
  A role change or a disabled account revokes sessions immediately. Logout uses OIDC back-channel logout.
- **Passwords** follow [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html): a long minimum length,
  no composition rules or forced periodic changes, a blocklist of breached and common passwords, and argon2id hashing
  configured in the Keycloak password policy. Login is rate-limited, and Keycloak brute-force lockout uses backoff.
- **MFA is mandatory** for `platform_admin`, `security_operator` and `approver`. **Step-up re-authentication**
  (`max_age` / `acr`) is required before activating the kill switch, publishing a policy, changing a role or rotating keys.

### 1B.2 Signup (deliberately limited)
- **Organisation users are invite-only.** An admin sends an invite (a signed one-time token valid for 72 hours, bound to an email and a role).
  An SSO-enabled workspace can instead auto-provision users through domain-verified OIDC with a default `viewer` role.
- **Public self-signup only creates a demo sandbox workspace.** It requires a verified email and
  [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/), is rate-limited by IP and email, and is limited to the mock upstream.
  It has quotas (agents, requests per day) and is deleted automatically after 7 days.
- **First admin of a fresh install:** a CLI bootstrap command (`veyrqun admin bootstrap`), never a web form.
- **Agents are not users.** They authenticate with the workload JWTs from Phase 1 (client credentials scoped to a
  workspace, short-lived, rotatable). The secret is shown once at creation and only its hash is stored.

### 1B.3 Roles, tenancy and separation of duties
| Role | Can | Cannot |
|---|---|---|
| `platform_admin` | Manage workspaces, users, roles, IdP config and keys | Approve their own changes |
| `security_operator` | Edit and draft policies, revoke or stop agents, use the fleet kill switch | Publish a production policy without an approver |
| `approver` | Approve or reject `require_approval` actions and policy publishes | Approve a request they raised |
| `auditor` | Read and export the audit log, run chain verification | Change anything (read-only; cannot also hold operator roles) |
| `app_owner` | Register agents and rotate credentials in their workspace, see their agents' decisions and spend, submit policy change requests | Publish policies or see other workspaces |
| `viewer` | Read-only dashboards for their workspace | Change anything |

- **Maker-checker:** a production policy publish, a role grant to admin, and kill-switch *release* (but not activation)
  each need a second person. The system blocks self-approval and records both identities in the audit chain.
- **Separation-of-duties check:** assigning a conflicting role combination (for example `auditor` together with `security_operator`) is rejected.
- **Accountable owner:** every agent has an `accountable_owner` (a named human) and a `business_service`. This supports
  accountability regimes and the model/agent inventory.
- **Tenancy:** every row carries `workspace_id`, enforced with
  [Postgres row-level security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html). The app sets
  `SET LOCAL app.workspace_id` per transaction; tests prove that cross-workspace reads return 0 rows.
- **Authorisation tests:** generate tests from the role table above, covering every endpoint × every role
  → expected 200/403, plus ID-swap tests for insecure direct object references (IDOR).
- **Auth events** (login, failure, MFA enrolment, step-up, role change, invite, session revoke) go into the hash-chained audit log.

### 1B.4 Data security
- **In transit:** TLS everywhere with HSTS; mTLS between services on Kubernetes (Phase 5). Render and Vercel terminate public TLS.
- **At rest:** provider disk encryption, plus **application-level envelope encryption** (AES-256-GCM) for sensitive
  columns: stored payloads (when `AUDIT_PAYLOADS=true`), invite emails, and webhook secrets. Each workspace has its own data key,
  wrapped by a master key from a KMS ([AWS KMS](https://docs.aws.amazon.com/kms/), GCP KMS or Azure Key Vault on Kubernetes;
  a Render secret environment variable in the demo). A `kid` on each value lets keys rotate.
- **Minimisation:** by default store **hashes** of prompts and completions, not the text. Never store card numbers (PANs); if
  one must be displayed, mask it to the first 6 and last 4 digits. Redact before anything is persisted or logged.
- **Erasure and immutability:** the audit log records a pseudonymous `actor_id`. The identity mapping is encrypted
  with a per-user key, and deleting the user deletes the key (crypto-shredding). The hash chain stays valid while the person
  can no longer be identified.
- **Append-only at the database level:** the app's database role has `INSERT`/`SELECT` only on `audit.*`, a trigger
  rejects `UPDATE`/`DELETE`, and migrations run under a separate role.
- **Retention**, configurable per data class:
  - decision and audit logs: at least 1 year online, then archived
  - security/auth logs: at least 180 days, and stored in India for Indian deployments
  - sandbox data: 7 days
  - Scheduled jobs enforce these rules and log every purge.
- **Secrets:** Render [environment variables and secret files](https://render.com/docs/configure-environment-variables) (`sync: false` in the Blueprint), or [External Secrets Operator](https://external-secrets.io/) on Kubernetes.
  **Nothing secret in `VITE_*` variables**, because they are bundled into the public JavaScript.
- **Browser hardening:** a strict CSP, `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Backups:** Neon point-in-time restore (branching) or managed backups. Document RPO/RTO and record one restore drill in `docs/runbooks/restore.md`.
- **Data residency:** the hosted demo (Render Singapore) uses **synthetic data only**. Real Indian bank deployments run in an Indian region
  (for example GKE `asia-south1`) via Helm values.

### 1B.5 Storage and retrieval
- Postgres schemas:
  - `iam`: users mirrored by IdP `sub`, workspaces, memberships, invites, auth events
  - `gov`: agents, policy versions, change requests, approvals
  - `audit`: decisions, operator events, anchors, with **monthly range partitions** and a BRIN index on `ts`
  - `rag`: pgvector chunks
- Redis holds sessions, counters, rate windows, pub/sub and streams. Nothing in Redis is the system of record except the
  live counters, which are reconciled against Postgres (1B.6).
- List endpoints use **keyset pagination** (`WHERE (ts, id) < ($1, $2) ORDER BY ts DESC, id DESC`) with page size ≤ 200,
  and composite indexes that match the dashboard filters (workspace, agent, outcome, rule, time range).
- **Audit export** streams NDJSON or CSV with a signed manifest: range, record count, first and last hash, and the anchor
  it chains to. An external auditor can verify it offline with `scripts/verify_export.py`.
- Dashboard aggregates come from Prometheus or from materialized views refreshed every minute, never from full-table scans.

### 1B.6 Validation and verification
- **At the edge:** pydantic v2 models with `extra="forbid"`, and zod `.strict()` in the gateway.
  Limits on body size (1 MB by default), string lengths and array sizes; enums for roles and action types;
  RFC 3339 UTC timestamps; ISO 4217 currency codes.
- **Business rules:** caps ≥ 0; `max_per_transaction ≤ spend_cap_daily`; `allowed_actions ∩ blocked_actions = ∅`;
  `accountable_owner` must be an active user; a policy publish must pass `opa check`, all tests, and a dry run
  against recent traffic (Phase 9a's dry-run diff, reused here).
- **Integrity jobs:** run chain and anchor verification nightly (alert on failure), and reconcile Redis spend counters against
  the sum of approved decisions (alert if they drift by more than 0.01). Both jobs write a signed result into the audit log.
- **Security testing in CI:** [OWASP ZAP baseline](https://www.zaproxy.org/docs/docker/baseline-scan/) against the
  preview deployment, [Semgrep](https://semgrep.dev/docs/), dependency audit, and the authorisation tests from 1B.3.
  Use [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) Level 2 as the checklist
  and record its status in `docs/security/asvs.md`.

**Done when:** login, invite and sandbox-signup flows have end-to-end tests ([Playwright](https://playwright.dev/));
the authorisation tests and RLS tests pass; MFA and step-up are enforced on the sensitive actions; maker-checker blocks
self-approval; and the encryption, crypto-shredding and append-only tests pass.

---

## Phase 2: Inline gateway (Node 22 + TypeScript), the core deliverable

Stack: [Fastify](https://fastify.dev/docs/latest/), [undici](https://undici.nodejs.org/) for upstream calls,
[jose](https://github.com/panva/jose) for JWT/JWKS, [@open-policy-agent/opa-wasm](https://github.com/open-policy-agent/npm-opa-wasm)
for in-process evaluation ([OPA WASM docs](https://www.openpolicyagent.org/docs/latest/wasm/)),
[ioredis](https://github.com/redis/ioredis), [zod](https://zod.dev/) for config and validation,
[Vitest](https://vitest.dev/) for tests, `tsc --strict`, and ESLint.

Request pipeline (every step adds a span and a timing):
1. **Authenticate:** verify the agent JWT against the cached JWKS; reject with `401`.
2. **Identify the action:**
   - `POST /v1/chat/completions` ([OpenAI-compatible](https://platform.openai.com/docs/api-reference/chat)) → `action=llm.chat`;
     amount = estimated token cost from a model price table. Correct it from the `usage` field after the response.
   - `POST /mcp` ([MCP spec](https://modelcontextprotocol.io/specification), JSON-RPC over Streamable HTTP):
     `tools/call` → `action=tool:<name>`. `amount` is taken from the arguments using a per-tool mapping in the bundle data.
     Filter `tools/list` responses down to the tools this agent is allowed to call.
3. **Classify data:** detect PII (email, phone, Aadhaar/PAN, IBAN) and PCI (card numbers with a
   [Luhn check](https://en.wikipedia.org/wiki/Luhn_algorithm)) in the request. Emit `data_classes[]` and redaction spans.
   An optional guard-model score is added in Phase 9.
4. **Decide:** evaluate the in-process WASM policy with
   `{agent, env, action, amount, data_classes, signals, fleet_state}` and get
   `{outcome, rule_id, redactions, policy_version}`.
5. **Enforce counters:** run the Redis Lua scripts (atomic check + increment). Roll back the reservation if the upstream call fails.
6. **Proxy:**
   - Stream SSE responses chunk by chunk with backpressure.
   - Scan responses with an overlapping buffer so PII split across chunks is still caught.
     Redact, or abort the stream according to policy.
   - Set connect and total timeouts per upstream. Retry only idempotent calls. Fail over across configured
     providers (tiered routing).
7. **Emit a decision event** to a [Redis Stream](https://redis.io/docs/latest/develop/data-types/streams/)
   (`XADD`, non-blocking). The event includes `decision_id`, actor, action, outcome, rule, policy_version,
   payload hashes (not raw payloads unless `AUDIT_PAYLOADS=true`), and timings.
8. **Modes:** an `observe` or `enforce` setting for each app or agent and each data class.
   In observe mode, log what *would* have happened and allow the request.

Control channels:
- **Kill switch / revoke:** subscribe to Redis [pub/sub](https://redis.io/docs/latest/develop/interact/pubsub/)
  `veyrqun:control`, and also poll a fleet-state version key every second as a backstop.
- **Bundles:** poll `GET /bundles/current` with `If-None-Match`. Hot-swap the WASM module atomically and keep the last good bundle.

Also build: a mock upstream LLM (deterministic and offline, used in CI) and an `UPSTREAM_*` config for real
OpenAI-compatible providers (OpenRouter, Groq, or a local Ollama).
Endpoints: `/healthz` (process up) and `/readyz` (bundle loaded and Redis reachable), plus `/metrics` with
[prom-client](https://github.com/siimon/prom-client).

**Tests:** unit tests for every pipeline step, an integration test per row of the §4 table, a streaming
redaction test with PII split across chunks, and an MCP `tools/list` filtering test.
**Done when:** all tests pass, and `loadtest/gateway.js` reports gateway overhead (total time minus mock-upstream time)
to `docs/results/gateway_latency.json`. The target is p99 < 10 ms on one host.

---

## Phase 3: Audit pipeline

- `audit-writer` consumes the Redis Stream through a consumer group with **a single active writer** for the global chain.
  Write an ADR on the trade-off: a single chain is simple and strict, but per-tenant or per-agent chains scale further.
- Each record is `SHA-256(prev_hash ‖ canonical_json(fields))`, and also **HMAC-SHA256 signed** with a key ID
  (`kid`) so the key can be rotated. Each record can be verified on its own: `GET /audit/{id}/verify`.
- Make it **idempotent** by using `decision_id` as the unique key, so a redelivered event never forks the chain.
- **Anchoring (Tech Debt #5):** every N minutes, write the head hash to an append-only `anchors` table and optionally
  to an [S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html) bucket.
  `verify_chain` checks against the anchors.
- Record backlog and lag metrics, plus alerts.

**Done when:** the tamper, delete and reorder tests fail verification at the exact entry, the redelivery test
produces no duplicate records, and the HMAC key-rotation test passes.

---

## Phase 4: LangGraph agent fleet and human approval

- In `agents/`, build [LangGraph](https://github.com/langchain-ai/langgraph) agents: fee reversal, card replacement,
  travel rebooking, and a **rogue agent** (with prompt-injection and over-limit scenarios).
- All model and tool traffic goes **through the gateway URL**. The agent code contains no governance logic.
- Build a mock "bank core" MCP server exposing `waive_fee`, `card_issue`, `card_lock`, `flight_rebook`, `credit_limit_increase`,
  `wire_transfer` and so on. These match the existing action types.
- **Human-in-the-loop:** when the gateway returns `202 require_approval`, the graph calls
  [`interrupt()`](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) and resumes after the
  dashboard approves. Use the [Postgres checkpointer](https://langchain-ai.github.io/langgraph/concepts/persistence/)
  so a stopped agent's state survives restarts.
- Tracing: [Langfuse](https://langfuse.com/docs). Put the gateway `decision_id` in the trace metadata.
- `agents/scenarios.py` runs every scenario and writes `docs/results/scenarios.json` (expected vs actual outcome per case).

---

## Phase 5: Kubernetes and Helm

- Chart at `deploy/helm/veyrqun` ([Helm docs](https://helm.sh/docs/), [best practices](https://helm.sh/docs/chart_best_practices/)):
  - the gateway, control plane and audit writer, each with a Deployment, Service, probes, resource limits and a
    `securityContext` (non-root, read-only root filesystem)
  - an [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) and
    [PodDisruptionBudget](https://kubernetes.io/docs/tasks/run-application/configure-pdb/) for the gateway
  - [NetworkPolicies](https://kubernetes.io/docs/concepts/services-networking/network-policies/):
    default-deny, and **only the gateway may send egress** to provider CIDRs or an egress proxy
  - ingress through [Gateway API](https://gateway-api.sigs.k8s.io/) (with an ingress-nginx option)
  - optional mTLS profiles for [Linkerd](https://linkerd.io/2/getting-started/) or [Istio](https://istio.io/latest/docs/)
  - Postgres and Redis either bundled (dev) or external (`values-production.yaml`)
  - a `values.schema.json`, and a `helm test` hook that sends one allowed and one blocked request
- CI: `helm lint`, [kind](https://kind.sigs.k8s.io/) via [helm/kind-action](https://github.com/helm/kind-action),
  install the chart, run the smoke tests.
- `docs/install.md`: a customer-style install guide for EKS, AKS and GKE (values, TLS, egress, rollback).
  Do one real deployment on [GKE Autopilot](https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview)
  or AKS on free credits, and record it (with screenshots) in `docs/results/k8s_deploy.md`.

---

## Phase 6: Hosted demo on Render (backend) and Vercel (frontend)

Render runs the backend as normal long-running containers (`VEYRQUN_RUNTIME=server`), so the gateway keeps its
pub/sub kill-switch listener, in-memory bundle cache and streaming exactly as in Kubernetes. Vercel serves only the
static React dashboard. Kubernetes (Phase 5) stays the reference deployment, and the ADR *"Hosted demo (Render) vs
in-cluster data plane"* records the differences.

**Render free-tier facts to design around** ([Render free plan](https://render.com/docs/free)):
- Free is available for **web services, Postgres, Key Value and static sites only**. Background workers, cron jobs and private services are paid.
- Free web services **spin down after 15 minutes idle** and take about **1 minute to wake**. The workspace gets 750 free instance hours per month.
  Free services can't receive private-network traffic, and local disk is lost on restart.
- Free Render Postgres **expires 30 days after creation**, so use [Neon](https://neon.com/docs/introduction) (free, persistent, pgvector) instead.
- Free Render Key Value is in-memory only (**data lost on restart**). Use [Upstash Redis](https://upstash.com/docs/redis/overall/getstarted)
  (free, persistent, `rediss://` TCP URL) or rebuild counters on startup (below).
- Regions are Oregon, Ohio, Virginia, Frankfurt and **Singapore** (no India). Use Singapore and put Neon and Upstash in Singapore too.
  The demo data is **synthetic only**. Real Indian deployments use Helm in an Indian region (rule in 1B.4).

**Blueprint (`render.yaml` at the repo root, [spec](https://render.com/docs/blueprint-spec)):**

| Service | Type / runtime | Notes |
|---|---|---|
| `veyrqun-gateway` | `web`, `runtime: docker` | `healthCheckPath: /readyz`; listens on `0.0.0.0:$PORT`; its own public URL that agents call |
| `veyrqun-control-plane` | `web`, `runtime: docker` | `healthCheckPath: /readyz`. **Free plan:** also runs the audit writer and scheduled jobs in-process (below). **Paid:** move them out |
| `veyrqun-audit-writer` | `worker` (**paid only**) | Optional split once on a paid plan |
| `veyrqun-jobs` | `cron` (**paid only**) | Optional; replaces the in-process scheduler |
| `veyrqun-idp` | `web`, docker (Keycloak, **paid 2 GB instance**) | Optional. A free 512 MB instance is too small for Keycloak, so the free demo uses a hosted OIDC provider (for example Auth0 or Microsoft Entra External ID) configured to the 1B rules |

- Use `envVarGroups` for shared settings. Secrets are `sync: false` (set them in the dashboard), never committed.
- Set `autoDeployTrigger: checksPass` so a deploy happens only after GitHub Actions passes.
- Migrations: use `preDeployCommand: alembic upgrade head` where your instance type supports it. Otherwise run Alembic as a
  GitHub Actions step against the **unpooled** Neon URL before the deploy is triggered.

**Code changes for the free plan:**
- **In-process background work with leader election:** the control plane's FastAPI lifespan starts the audit-stream
  consumer and an [APScheduler](https://apscheduler.readthedocs.io/) scheduler (anchoring, nightly integrity and
  reconciliation, daily rollover, retention purges, sandbox cleanup). Only the instance holding a Redis lock
  (`SET key value NX PX`, renewed regularly) runs them. Every job is idempotent. The same code runs as the separate `worker`/`cron` on paid plans.
- **Spin-down catch-up:** while spun down, nothing runs. On startup, the leader drains the audit stream backlog, then runs any
  scheduled job whose last successful run (stored in Postgres) is overdue.
- **Counter recovery** (needed if you use Render Key Value): on startup, if the Redis counter keys are missing, rebuild
  today's spend and rate state from approved decisions in Postgres **before** `/readyz` returns 200. Until then the gateway fails closed.
- **Gateway → control plane** goes over public HTTPS (free services can't use the private network). Bundle fetches use a
  service token, and bundles are **signed** so the gateway verifies them. On paid plans switch to a private service and internal hostnames.
- **Cold starts:** the first request after idle takes about 1 minute. Show a "waking the demo" banner in the dashboard
  (poll `/readyz`). Add an optional manual `workflow_dispatch` GitHub Action that warms the services before a live demo.
  Don't rely on constant keep-alive pings, which use up the shared 750 free hours.
  To keep the gateway warm during interview weeks, move just that service to a paid starter instance.
- **Latency reporting:** report **warm** gateway overhead p99 and **cold-start** time separately in
  `docs/results/gateway_latency_render.json`. The in-cluster number stays the headline figure.

**Frontend on Vercel ([Vite guide](https://vercel.com/docs/frameworks/vite)) and session cookies:**
- `*.vercel.app` and `*.onrender.com` are on the Public Suffix List, so the browser treats the dashboard and the API as
  **different sites**, and the session cookie would be a blocked third-party cookie. Pick one fix:
  1. **Default:** [Vercel rewrites](https://vercel.com/docs/rewrites) so `/api/*` on the dashboard domain proxies to the
     control-plane Render URL. The browser sees one origin and cookies stay first-party. The live feed uses **SSE**
     through the same path. Add an end-to-end test that SSE streams through the rewrite (no buffering, with heartbeats every 15 s).
  2. **Alternative:** a custom domain with `app.<domain>` on Vercel and `api.<domain>` on Render ([Render custom domains](https://render.com/docs/custom-domains)).
     Requests between them are same-site, with CORS restricted to the app origin with credentials, never `*`.
- `VITE_*` variables hold only public values (rule in 1B.4).

**Other:**
- Telemetry: the OTLP batch exporter goes straight to Grafana Cloud (7B). Flush on SIGTERM, because Render sends it before a restart.
- Backups: Neon point-in-time restore. Record a restore drill in `docs/runbooks/restore.md`.
- Add a "demo mode" with seeded agents and a mock upstream, so the public demo needs no paid API keys.
- `docs/Deployment.md`: write it for Render + Vercel, docker compose and Helm.

---

## Phase 7: Observability, load, chaos and on-call

- [OpenTelemetry](https://opentelemetry.io/docs/) traces in the gateway ([JS](https://opentelemetry.io/docs/languages/js/))
  and control plane ([Python](https://opentelemetry.io/docs/languages/python/)), with one trace spanning agent → gateway → upstream.
- [Prometheus](https://prometheus.io/docs/) metrics and dashboards provisioned as code in [Grafana](https://grafana.com/docs/grafana/latest/):
  decisions by outcome and rule, overhead p50/p95/p99, Redis latency, bundle age, kill-propagation time,
  audit lag, and stream aborts.
- SLOs and alert rules in `deploy/observability/`.
- [k6](https://grafana.com/docs/k6/latest/) scenarios: steady, spike and soak. Results go to `docs/results/`.
- Chaos scripts: kill Redis, kill the control plane, kill a gateway pod mid-stream, and an audit-writer backlog.
  Assert that the §4 behaviour holds.
- Measure kill-switch propagation (activation to the last replica blocking) and write it to `docs/results/kill_switch.json`.
- `docs/runbooks/`: gateway 5xx spike, audit lag, stale bundle, key rotation.
  Write `docs/postmortems/` entries for real incidents found during these tests.
- CI additions: image scan with [Trivy](https://trivy.dev/), dependency audit, and SBOM.

### 7B. Admin console and app-owner portal (one React app, routes based on role)

**Observability stack (recommended): Grafana + the LGTM stack**: [Prometheus](https://prometheus.io/docs/) or Mimir for
metrics, [Loki](https://grafana.com/docs/loki/latest/) for logs, [Tempo](https://grafana.com/docs/tempo/latest/) for traces,
and [Grafana Alerting](https://grafana.com/docs/grafana/latest/alerting/).
- **Hosted demo:** send OTLP from the Render services to [Grafana Cloud](https://grafana.com/docs/grafana-cloud/) (free tier),
  flushing on SIGTERM, so no observability servers need hosting.
- **Kubernetes:** [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
  plus Loki and Tempo, enabled through chart values.
- **Grafana login:** Grafana signs in through the same Keycloak using [generic OAuth](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-authentication/generic-oauth/)
  with role mapping (`platform_admin`/`security_operator` → Editor, `auditor`/`approver` → Viewer).
  Anonymous access is off. **Do not iframe Grafana into the public demo**; link to it via SSO.
- **Dashboards are code** in `deploy/observability/dashboards/*.json`, and alert rules live in `deploy/observability/alerts/`.
  Alerts go to email or Slack.

**Admin console (`/admin/*`, visible according to the 1B.3 roles), for governance data that Grafana doesn't model well:**
- Fleet overview: live decision stream, approval rate, blocks by rule, spend against caps, kill-switch state and bundle version per gateway replica
- Kill switch and revoke controls (step-up + confirmation dialog; release needs a second person)
- Policy workspace: Rego editor, test results, dry-run diff, maker-checker publish, version history and rollback
- Approvals queue (`require_approval` actions and policy publishes), with SLA timers
- Audit explorer: filters, the decision trace for each record, single-record and full-chain verification, signed export
- Identity: users, invites, roles, SoD warnings, active sessions (revocable), and the auth-event log
- System health: SLO burn rate, gateway p99 overhead, audit lag, Redis and Postgres status
  (summary tiles from Prometheus, linking into Grafana for detail)
- Compliance view: agent inventory (owner, business service, models used, risk tier), retention-job status, latest integrity-job results

**App-owner portal (`/app/*`, scoped to the user's workspace):**
- Register agents, rotate credentials (shown once), set `observe`/`enforce` mode per agent (an enforce→observe change needs approval)
- Per agent: decisions, block reasons with decision traces, spend and token cost against caps, latency
- Submit policy change requests and track their status; see approvals they raised
- Quickstart snippet showing how to point an agent's LLM client and MCP URL at the gateway

**UI implementation:** React + Vite (existing), with [TanStack Query](https://tanstack.com/query/latest) for server state and
[React Router](https://reactrouter.com/) route guards driven by `/me` permissions. Guards are for the UX only;
the server still enforces every permission (rule 10). [Recharts](https://recharts.org/) is fine for summary tiles, but
detailed time-series charts belong in Grafana. Playwright end-to-end tests cover one flow per role.

---

## Phase 8: Governed RAG

- `rag/ingest.py`: chunk the policy and terms documents (synthetic, labelled as such) into Postgres + **pgvector**.
  Each chunk gets `clearance`, `data_class` and `source` labels.
- Expose retrieval as an MCP tool `search_docs` behind the gateway:
  - **permission filter before the search runs** (a SQL `WHERE` on the agent's clearance and scopes from the JWT and policy data)
  - hybrid retrieval (vector + Postgres full-text), then a rerank step
  - PII redaction on the retrieved text
  - **chunk IDs recorded in the decision event**
- Evaluation with a golden set of 30–50 questions plus adversarial cross-clearance queries
  ([Ragas](https://docs.ragas.io/) or your own script) → `docs/results/rag_eval.json`:
  leakage count (target 0), redaction recall, faithfulness and answer relevance.
- Optional: an operator copilot that answers "why was X blocked?" using RAG over policies, policy history and the audit log.
  It must cite audit entry IDs and is read-only.

---

## Phase 9: Models (optional stretch; never in the decision path)

**9a. Policy copilot: fine-tune with [Unsloth](https://docs.unsloth.ai/) on a Google Colab T4**
- Notebook `ml/copilot_unsloth_t4.ipynb` ([Unsloth repo](https://github.com/unslothai/unsloth), [TRL SFT](https://huggingface.co/docs/trl/sft_trainer)).
- Base model: `unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit` (or a 3B model if memory allows). QLoRA in 4-bit, **fp16** (the T4 has no bf16),
  `max_seq_length=2048`, LoRA r=16, batch size 2 × gradient accumulation 4, 1–2 epochs, train on responses only.
  Checkpoint to Google Drive and push the adapter to the Hugging Face Hub.
- Data: `ml/gen_copilot_data.py` builds 1.5–3k English → Rego + tests pairs from the policy schema, and **keeps only pairs that
  pass `opa check` and `opa test`**. Hold out 150–200 pairs with unseen phrasings. Label the data synthetic.
- Eval (`ml/eval_copilot.py`, base vs fine-tuned): compile rate, generated-test pass rate, and semantic agreement on 20
  probe requests per policy → `docs/results/copilot_eval.json`.
- Serving: export to GGUF for [llama.cpp](https://github.com/ggml-org/llama.cpp) / [Ollama](https://ollama.com) locally,
  with an API model as fallback in the hosted demo.
- In the product, a draft must compile, pass its tests, and run a **dry run against the last 1,000 audited requests**
  (a diff of decisions that would change) before an `operator` approves it. It is never deployed automatically.

**9b. Guard classifier (optional signal)**
- Fine-tune a small encoder model (DeBERTa-v3-small or ModernBERT-base) for prompt-injection detection with the
  Hugging Face Trainer on the T4. Export it to [ONNX Runtime](https://onnxruntime.ai/docs/) through
  [Optimum](https://huggingface.co/docs/optimum/).
- Run it as a sidecar called by the gateway with a strict timeout. A timeout or error sends `signals.injection_score = null`,
  and Rego decides what to do with that value. Report precision, recall and added latency.

---

## Phase 10: Docs and results

- README: problem statement, architecture diagram (redrawn for v3), a 5-minute quickstart (docker compose), Helm install,
  Render + Vercel demo links, a results table generated from `docs/results/*.json` by `scripts/render_results.py`,
  the attribution credit, and limitations.
- `docs/adr/`: at least these ADRs: inline gateway vs SDK, OPA-WASM in-process vs sidecar, Redis counters + fail-closed,
  async audit + single writer, deterministic decisions and the model boundary, hosted demo (Render + Vercel) vs in-cluster data plane (Kubernetes).
- `docs/COMPLIANCE.md`: an **engineering control map, not legal advice and not a certification**.
  Write "designed to support", never "compliant". Use the columns *requirement → VEYRQUN control →
  evidence (test, job output, screenshot)*. Cover at least:

  | Jurisdiction / standard | Requirements to map |
  |---|---|
  | India: [DPDP Act 2023 + DPDP Rules 2025](https://www.hlc.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-) | Most obligations apply from 13 May 2027: security safeguards (encryption, masking, access control, monitoring), breach notification to the Board without delay plus a detailed report within 72 hours, logs kept ≥ 1 year, erasure when consent is withdrawn |
  | India: [CERT-In Directions (2022)](https://psalegal.com/new-cert-in-directions-overview-and-implications/) | Report incidents within 6 hours, keep ICT logs for 180 days inside India, synchronise clocks (NTP) |
  | India: [RBI FREE-AI framework (Aug 2025)](https://kpmg.com/in/en/insights/2025/08/rbi-free-ai-committee-report-on-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence.html) | 7 Sutras (incl. accountability, understandable by design, safety and resilience); board-approved AI policy; audits and continuous monitoring; explainability; AI-aware cybersecurity |
  | India: RBI IT governance and payment data storage directions | Data localisation for payment data → Indian region for real deployments |
  | UK (Barclays): FCA Consumer Duty, SM&CR, [PRA SS1/23 model risk](https://www.bankofengland.co.uk/prudential-regulation/publication/2023/may/model-risk-management-principles-for-banks-ss), operational resilience, UK GDPR | Accountable owner per agent, model/agent inventory, impact tolerances → kill switch and RTO, data-subject rights |
  | EU: [AI Act](https://artificialintelligenceact.eu/) | Logging/record-keeping (Art. 12), human oversight (Art. 14); credit scoring is Annex III high-risk. Under the [provisional Omnibus agreement](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement), Annex III obligations move to 2 Dec 2027, pending publication in the Official Journal. Check the current status before citing it |
  | EU: [DORA](https://www.eiopa.europa.eu/digital-operational-resilience-act-dora_en), GDPR | ICT incident classification and reporting, third-party risk, resilience testing; GDPR data minimisation and erasure |
  | US: GLBA Safeguards Rule, SR 11-7, [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework), SOC 2 | Safeguards program, model risk, the govern/map/measure/manage functions, the vendor control evidence a startup's customers expect |
  | Cross-cutting | [PCI DSS v4.0.1](https://www.pcisecuritystandards.org/document_library/) (PAN masking, MFA, logging), [ISO/IEC 27001](https://www.iso.org/standard/27001), [ISO/IEC 42001](https://www.iso.org/standard/42001), [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html), [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/), [OWASP Top 10 for LLM Apps](https://genai.owasp.org/llm-top-10/) |
- `docs/AI_WORKFLOW.md`: how AI tools were used in building this project and how their output was verified (tests, parity checks, review).
- Update `docs/Technical Debt.md`: mark #1–#7 resolved with links to the relevant code, and add any new open items.

---

## 5. Coding standards

- **TypeScript:** `strict`, no `any` without a comment, zod for all external input, and errors returned as typed results
  at module boundaries.
- **Python:** 3.11+, type hints, `ruff` + `mypy --strict` for new modules, pydantic v2 models at the API edges.
- **Every request has a `decision_id`**, which is propagated in the `x-veyrqun-decision-id` response header and in logs and traces.
- **Logs are structured JSON.** Never log raw prompts, completions or PII unless `AUDIT_PAYLOADS=true` is set.
- **Config is only env vars, validated at startup.** Fail fast on invalid config.
- **Containers are multi-stage, non-root, and pinned to a base image digest.**

## 6. Definition of done (every phase)

- [ ] Tests added, and all suites green locally and in CI
- [ ] Docs and ADR updated, plus `.env.example` updated if config changed
- [ ] Any new metric produced by a script into `docs/results/`
- [ ] No secrets in the code; attribution intact
- [ ] Every new endpoint added to the role matrix and the generated authorisation tests; new data fields classified (retention, encryption)
- [ ] Short summary of what changed and how to verify it, posted in the commit or PR body

## 7. Reference links

| Topic | Links |
|---|---|
| Origin / license | [Aegis (infyier)](https://github.com/infyier/aegis-governance) · [MIT License](https://opensource.org/license/mit) |
| Policy | [OPA docs](https://www.openpolicyagent.org/docs/latest/) · [Rego](https://www.openpolicyagent.org/docs/latest/policy-language/) · [Testing](https://www.openpolicyagent.org/docs/latest/policy-testing/) · [Bundles](https://www.openpolicyagent.org/docs/latest/management-bundles/) · [WASM](https://www.openpolicyagent.org/docs/latest/wasm/) · [npm-opa-wasm](https://github.com/open-policy-agent/npm-opa-wasm) |
| Gateway | [Fastify](https://fastify.dev/docs/latest/) · [undici](https://undici.nodejs.org/) · [jose](https://github.com/panva/jose) · [MCP spec](https://modelcontextprotocol.io/specification) · [OpenAI Chat API](https://platform.openai.com/docs/api-reference/chat) · [Node crypto (HMAC)](https://nodejs.org/api/crypto.html) |
| State / data | [Redis Lua](https://redis.io/docs/latest/develop/interact/programmability/eval-intro/) · [Streams](https://redis.io/docs/latest/develop/data-types/streams/) · [Pub/Sub](https://redis.io/docs/latest/develop/interact/pubsub/) · [SQLAlchemy 2](https://docs.sqlalchemy.org/en/20/) · [Alembic](https://alembic.sqlalchemy.org/) · [pgvector](https://github.com/pgvector/pgvector) |
| Kubernetes | [Helm](https://helm.sh/docs/) · [NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/) · [HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) · [PDB](https://kubernetes.io/docs/tasks/run-application/configure-pdb/) · [Gateway API](https://gateway-api.sigs.k8s.io/) · [kind](https://kind.sigs.k8s.io/) · [GKE Autopilot](https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview) |
| Hosting | [Render free plan](https://render.com/docs/free) · [Blueprint spec](https://render.com/docs/blueprint-spec) · [Render regions](https://render.com/docs/regions) · [Render env vars](https://render.com/docs/configure-environment-variables) · [Render custom domains](https://render.com/docs/custom-domains) · [Neon](https://neon.com/docs/introduction) · [Neon pgvector](https://neon.com/docs/extensions/pgvector) · [Upstash Redis](https://upstash.com/docs/redis/overall/getstarted) · [APScheduler](https://apscheduler.readthedocs.io/) · [Vercel + Vite](https://vercel.com/docs/frameworks/vite) · [Vercel rewrites](https://vercel.com/docs/rewrites) · [Vercel env vars](https://vercel.com/docs/environment-variables) |
| Observability / testing | [OpenTelemetry](https://opentelemetry.io/docs/) · [Prometheus](https://prometheus.io/docs/) · [Grafana](https://grafana.com/docs/grafana/latest/) · [k6](https://grafana.com/docs/k6/latest/) · [Trivy](https://trivy.dev/) · [Vitest](https://vitest.dev/) · [GitHub Actions](https://docs.github.com/en/actions) |
| Agents / RAG | [LangGraph](https://github.com/langchain-ai/langgraph) · [Human-in-the-loop](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) · [Persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/) · [Langfuse](https://langfuse.com/docs) · [Ragas](https://docs.ragas.io/) |
| Models | [Unsloth docs](https://docs.unsloth.ai/) · [Unsloth GitHub](https://github.com/unslothai/unsloth) · [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer) · [llama.cpp](https://github.com/ggml-org/llama.cpp) · [Ollama](https://ollama.com) · [ONNX Runtime](https://onnxruntime.ai/docs/) · [Optimum](https://huggingface.co/docs/optimum/) |
| Identity / security | [Keycloak](https://www.keycloak.org/documentation) · [OAuth PKCE](https://oauth.net/2/pkce/) · [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html) · [Postgres RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) · [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) · [OWASP ZAP baseline](https://www.zaproxy.org/docs/docker/baseline-scan/) · [Semgrep](https://semgrep.dev/docs/) · [Turnstile](https://developers.cloudflare.com/turnstile/) · [External Secrets](https://external-secrets.io/) · [Playwright](https://playwright.dev/) |
| Dashboards | [Grafana generic OAuth](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-authentication/generic-oauth/) · [Grafana Cloud](https://grafana.com/docs/grafana-cloud/) · [Loki](https://grafana.com/docs/loki/latest/) · [Tempo](https://grafana.com/docs/tempo/latest/) · [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) · [TanStack Query](https://tanstack.com/query/latest) · [React Router](https://reactrouter.com/) |
| Regulation | [DPDP Rules summary](https://www.hlc.com/en/publications/indias-digital-personal-data-protection-act-2023-brought-into-force-) · [CERT-In Directions](https://psalegal.com/new-cert-in-directions-overview-and-implications/) · [RBI FREE-AI](https://kpmg.com/in/en/insights/2025/08/rbi-free-ai-committee-report-on-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence.html) · [EU AI Act](https://artificialintelligenceact.eu/) · [AI Act Omnibus](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement) · [DORA](https://www.eiopa.europa.eu/digital-operational-resilience-act-dora_en) · [PRA SS1/23](https://www.bankofengland.co.uk/prudential-regulation/publication/2023/may/model-risk-management-principles-for-banks-ss) · [NIST AI RMF](https://www.nist.gov/itl/ai-risk-management-framework) · [PCI DSS](https://www.pcisecuritystandards.org/document_library/) · [OWASP LLM Top 10](https://genai.owasp.org/llm-top-10/) |
| Process | [Conventional Commits](https://www.conventionalcommits.org/) · [ADRs](https://adr.github.io/) · [Copilot repo instructions](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot) · [AGENTS.md](https://agents.md/) |
