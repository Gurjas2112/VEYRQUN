# 0006. Integration tests run only against PostgreSQL (no SQLite fallback)

**Status:** accepted · 2026-10-22 · *deviates from the spec's "keep a SQLite option for fast unit tests"*

## Context
v3 relies on Postgres-specific behaviour: schemas, `NUMERIC(18,2)`, range partitioning, advisory locks, row-level
security (Phase 1B), triggers and role grants. A SQLite variant would test a different system.

## Decision
- Pure units (oracle, money, ids, crypto, and Rego via `opa test`) need no database at all.
- Everything that touches storage runs against real Postgres 17 + Redis: docker compose locally, service containers in
  CI. Tests connect as the restricted `veyrqun_app` role, so the grants are exercised too.

## Consequences
Running the full suite needs `docker compose up -d postgres redis` (about 15 s). A SQLite fallback would make tests
look green while the Postgres-only controls (append-only grants, RLS) were broken.
