"""Support for migrating v2 (SQLite) data: id mapping + verbatim, append-only legacy audit history.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = r"""
CREATE SCHEMA IF NOT EXISTS migration;

CREATE TABLE migration.id_map (
    run_id      uuid NOT NULL,
    entity      text NOT NULL,
    legacy_id   text NOT NULL,
    new_id      uuid NOT NULL,
    PRIMARY KEY (entity, legacy_id)
);

CREATE TABLE migration.runs (
    run_id       uuid PRIMARY KEY,
    source       text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    report       jsonb
);

-- v2 audit rows are kept byte-for-byte (original hash fields), so the v2 chain stays verifiable.
CREATE TABLE audit.legacy_v2_events (
    legacy_rowid    bigint PRIMARY KEY,
    action_id       text NOT NULL UNIQUE,
    agent_id        text NOT NULL,
    action_type     text NOT NULL,
    amount_raw      double precision NOT NULL,
    amount          numeric(18,2) NOT NULL,
    legacy_ts       text NOT NULL,
    outcome         text NOT NULL,
    rule_triggered  text,
    policy_version  integer NOT NULL,
    prev_hash       text NOT NULL,
    entry_hash      text NOT NULL,
    run_id          uuid NOT NULL
);
CREATE TRIGGER legacy_append_only BEFORE UPDATE OR DELETE ON audit.legacy_v2_events
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veyrqun_app') THEN
        GRANT USAGE ON SCHEMA migration TO veyrqun_app;
        GRANT SELECT ON ALL TABLES IN SCHEMA migration TO veyrqun_app;
        GRANT SELECT ON audit.legacy_v2_events TO veyrqun_app;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit.legacy_v2_events; DROP SCHEMA IF EXISTS migration CASCADE;")
