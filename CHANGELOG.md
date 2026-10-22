# Changelog

This project uses [Conventional Commits](https://www.conventionalcommits.org/). Versions follow
[Semantic Versioning](https://semver.org/).

## Provenance

| Version | Author | What it is |
|---|---|---|
| **v1: Aegis** | [infyier](https://github.com/infyier/aegis-governance) (MIT) | Original hackathon build for American Express CodeStreet 2026: a single-file FastAPI policy API (status, block-list, allow-list, daily spend cap), SHA-256 hash-chained audit log, 5-persona simulator, WebSocket feed, and a single-file React dashboard. |
| **v2: VEYRQUN refactor** | Gurjas Singh Gandhi | Restructured and extended the v1 code (list below). |
| **v3: VEYRQUN gateway & platform** | Gurjas Singh Gandhi | New inline TypeScript gateway, OPA policy-as-code, Postgres/Redis control plane, identity and RBAC, signed audit pipeline, LangGraph fleet, governed RAG, Helm/GKE, hosted demo. In progress; see [docs/AGENT_PROMPT.md](docs/AGENT_PROMPT.md). |

Both copyright notices are kept in [LICENSE](LICENSE) as MIT requires.

## [Unreleased] — v3

### Added
- Attribution: both copyright lines in LICENSE, a README credit, and this changelog.
- `AGENTS.md`, `CONTRIBUTING.md`, `.editorconfig`, `.env.example`, and GitHub Actions CI (lint, tests, build, secret scan).
- `docs/AGENT_PROMPT.md`: the v3 build specification.
- Control plane on PostgreSQL + Redis + OPA/Rego (WASM); money as `NUMERIC(18,2)`/`Decimal`; Ed25519-signed policy
  bundles and agent JWTs; SQLite→Postgres migration with a data validator (ADRs 0002–0006).
- Identity and access: OIDC BFF (Keycloak), six roles, MFA/step-up, maker-checker, row-level-security tenancy,
  envelope encryption and crypto-shredding, signed audit export (ADRs 0007–0009).
- Inline TypeScript gateway for LLM (`/v1/chat/completions`, SSE) and MCP traffic, with streaming PII scanning and the
  §4 failure contract (ADRs 0010–0011).
- Signed audit pipeline: `python -m veyrqun.audit_writer` (single-leader Redis Streams consumer, idempotent on
  `decision_id`, dead-letter stream), hash v2 covering `details`, per-entry HMAC with key rotation, Ed25519-signed
  head anchors with optional S3 copies, `GET /audit/pipeline`, `GET /audit/anchors` (ADR 0012).

### Fixed
- The v2 commit replaced the original author's copyright line in LICENSE. It has been restored.

## [2.0.0] — VEYRQUN refactor of Aegis

Changed relative to Aegis v1:

- Split the 680-line `app.py` into the `veyrqun` package (policy engine, service layer, audit, metrics, routes, simulator, WebSocket).
- Split the 800-line `App.jsx` into components and hooks, and fixed a syntax error that broke the build.
- The simulator and the API now share one decision path (`service.attempt_action`). The simulator previously duplicated the logic.
- Simulator errors are logged instead of silently swallowed.
- Added 45 pytest tests covering accuracy, precedence, concurrency, tamper detection and latency budgets.
- Pooled SQLite connections cut full-decision p50 from ~5.9 ms to ~98 µs (measured by `backend/benchmark.py`).
- Added a `revoked` status that survives fleet resume, distinct from stop.
- Added per-transaction limits, per-action caps and per-minute rate limits on top of the daily cap.
- Added a `policy_versions` table, a history UI and forward-only rollback.
- Every operator action is now an `operator:*` entry in the hash chain.
- Added `/metrics` (JSON + Prometheus), a metrics panel, agent registration, and five demo scenarios.
- Rebranded Aegis → VEYRQUN and rewrote the docs, screenshots and screen recording.

## [1.0.0] — Aegis (infyier)

See the original repository: <https://github.com/infyier/aegis-governance>.
