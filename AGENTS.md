# AGENTS.md

Always-on instructions for AI coding agents (Claude Code, Cursor, Copilot) working in this repo.
The full phased specification is in [docs/AGENT_PROMPT.md](docs/AGENT_PROMPT.md).

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

