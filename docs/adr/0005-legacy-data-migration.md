# 0005. Migrating v2 data: verbatim legacy ledger plus an anchor, not re-hashing

**Status:** accepted · 2026-10-22

## Context
v2 stored money as REAL (float) in SQLite and hashed audit rows with a different field set. Re-hashing v2 history into
the v3 chain would *destroy* the evidence that the history is untampered.

## Decision
- `scripts/migrate_sqlite_to_pg.py` runs extract, transform and load in one Postgres transaction. Floats are converted
  via exact `Decimal(repr(x))`; values not representable at 2 dp are **reported** as precision anomalies, never
  silently rounded. Ids map to UUIDv7 through `migration.id_map`. A workspace cannot be migrated into twice.
- The v2 chain is verified at the source first. The migration refuses a broken chain unless explicitly overridden.
- v2 audit rows are copied verbatim into append-only `audit.legacy_v2_events`. The v2 head hash is **anchored** into the
  v3 chain (`operator:legacy_import`, `decision_id = v2-head:<hash>`).
- `scripts/validate_migration.py` is independent of the migrator and runs 15 checks: row counts, id map, field and
  policy equality, exact money totals, outcome distribution, the v2 chain recomputed from the target copy, the anchor,
  the whole v3 chain, and a domain/null/range sweep.

## Consequences
v2 history stays verifiable with v2's own algorithm, and v3's tamper evidence now covers it through the anchor.
v2 `spend_used_today` is not carried over, because the meaning differs ("since manual reset" vs local-midnight
rollover). The migration report lists those values for reconciliation.
