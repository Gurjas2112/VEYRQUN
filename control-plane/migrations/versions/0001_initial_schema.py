"""Initial v3 schema: iam / gov / audit on PostgreSQL.

Money is NUMERIC(18,2) + ISO 4217 currency. Ids are UUIDv7 (generated in the app).
audit.events is range-partitioned by month with a BRIN index on ts, and is append-only for the
runtime role `veyrqun_app` (INSERT/SELECT grants + a trigger that rejects UPDATE/DELETE).

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE = r"""
CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS gov;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE iam.workspaces (
    id          uuid PRIMARY KEY,
    slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    name        text NOT NULL,
    is_sandbox  boolean NOT NULL DEFAULT false,
    expires_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE iam.signing_keys (
    kid          text PRIMARY KEY,
    purpose      text NOT NULL CHECK (purpose IN ('agent_jwt', 'bundle', 'audit_hmac')),
    alg          text NOT NULL,
    public_jwk   jsonb,
    private_enc  bytea NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    retired_at   timestamptz
);

CREATE TABLE gov.agents (
    id                 uuid PRIMARY KEY,
    workspace_id       uuid NOT NULL REFERENCES iam.workspaces(id),
    agent_key          text NOT NULL CHECK (agent_key ~ '^[a-z0-9][a-z0-9_-]{2,63}$'),
    display_name       text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'stopped', 'revoked')),
    status_reason      text,
    mode               text NOT NULL DEFAULT 'enforce' CHECK (mode IN ('observe', 'enforce')),
    timezone           text NOT NULL DEFAULT 'UTC',
    currency           char(3) NOT NULL DEFAULT 'USD',
    accountable_owner  text,
    business_service   text,
    policy_version     integer NOT NULL DEFAULT 1,
    client_secret_hash text,
    secret_rotated_at  timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, agent_key)
);

CREATE TABLE gov.policy_versions (
    id                    uuid PRIMARY KEY,
    agent_id              uuid NOT NULL REFERENCES gov.agents(id) ON DELETE CASCADE,
    version               integer NOT NULL CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    created_by            text NOT NULL,
    note                  text,
    allowed_actions       text[] NOT NULL,
    blocked_actions       text[] NOT NULL,
    spend_cap_daily       numeric(18,2) NOT NULL CHECK (spend_cap_daily >= 0),
    max_per_transaction   numeric(18,2) CHECK (max_per_transaction >= 0),
    action_limits         jsonb NOT NULL DEFAULT '{}',
    action_daily_caps     jsonb NOT NULL DEFAULT '{}',
    rate_limit_per_minute integer CHECK (rate_limit_per_minute > 0),
    approval_threshold    numeric(18,2) CHECK (approval_threshold >= 0),
    redact_data_classes   text[] NOT NULL DEFAULT '{}',
    block_data_classes    text[] NOT NULL DEFAULT '{}',
    UNIQUE (agent_id, version),
    CHECK (max_per_transaction IS NULL OR max_per_transaction <= spend_cap_daily),
    CHECK (NOT (allowed_actions && blocked_actions))
);

CREATE TABLE gov.fleet_state (
    workspace_id uuid PRIMARY KEY REFERENCES iam.workspaces(id),
    stopped      boolean NOT NULL DEFAULT false,
    version      bigint NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   text
);

CREATE TABLE gov.approvals (
    id            uuid PRIMARY KEY,
    workspace_id  uuid NOT NULL REFERENCES iam.workspaces(id),
    agent_id      uuid REFERENCES gov.agents(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN ('action', 'policy_publish', 'kill_release', 'role_grant')),
    action_type   text,
    amount        numeric(18,2),
    currency      char(3),
    payload       jsonb NOT NULL DEFAULT '{}',
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'consumed')),
    requested_by  text NOT NULL,
    decided_by    text,
    reason        text,
    decision_id   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz,
    expires_at    timestamptz NOT NULL,
    CHECK (decided_by IS NULL OR decided_by <> requested_by)
);
CREATE INDEX approvals_pending_idx ON gov.approvals (workspace_id, status, created_at DESC);

CREATE TABLE gov.spend_snapshots (
    agent_id    uuid NOT NULL REFERENCES gov.agents(id) ON DELETE CASCADE,
    local_date  date NOT NULL,
    spend       numeric(18,2) NOT NULL,
    currency    char(3) NOT NULL,
    taken_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, local_date)
);

CREATE TABLE gov.bundle_revisions (
    revision     bigserial PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    reason       text NOT NULL
);

-- ---------------------------------------------------------------- audit ledger
CREATE SEQUENCE audit.events_seq AS bigint;

CREATE TABLE audit.events (
    seq             bigint NOT NULL,
    id              uuid NOT NULL,
    ts              timestamptz NOT NULL,
    workspace_id    uuid NOT NULL,
    actor           text NOT NULL,
    agent_id        text NOT NULL,
    action_type     text NOT NULL,
    amount          numeric(18,2) NOT NULL,
    currency        char(3) NOT NULL,
    outcome         text NOT NULL,
    mode            text NOT NULL DEFAULT 'enforce',
    rule_id         text,
    policy_version  integer NOT NULL,
    decision_id     text NOT NULL,
    prev_hash       text NOT NULL,
    entry_hash      text NOT NULL,
    PRIMARY KEY (seq, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE audit.events_default PARTITION OF audit.events DEFAULT;
CREATE INDEX events_ts_brin ON audit.events USING brin (ts);
CREATE INDEX events_ws_agent_ts ON audit.events (workspace_id, agent_id, ts DESC, seq DESC);
CREATE INDEX events_ws_outcome_ts ON audit.events (workspace_id, outcome, ts DESC, seq DESC);
CREATE INDEX events_decision ON audit.events (decision_id);

CREATE FUNCTION audit.ensure_partitions(months_ahead integer DEFAULT 3) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
    start_month date := date_trunc('month', now() - interval '1 month')::date;
    m date;
    created integer := 0;
    part text;
BEGIN
    FOR i IN 0..months_ahead + 1 LOOP
        m := (start_month + make_interval(months => i))::date;
        part := format('events_%s', to_char(m, 'YYYY_MM'));
        IF to_regclass(format('audit.%I', part)) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE audit.%I PARTITION OF audit.events FOR VALUES FROM (%L) TO (%L)',
                part, m, (m + interval '1 month')::date);
            created := created + 1;
        END IF;
    END LOOP;
    RETURN created;
END $$;
-- Runs with the owner's rights so the app role can roll partitions forward without CREATE.
ALTER FUNCTION audit.ensure_partitions(integer) SECURITY DEFINER SET search_path = audit, pg_temp;

SELECT audit.ensure_partitions(3);

CREATE FUNCTION audit.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit.events is append-only (% rejected)', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

-- ---------------------------------------------------------------- runtime role grants
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veyrqun_app') THEN
        GRANT USAGE ON SCHEMA iam, gov, audit TO veyrqun_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gov TO veyrqun_app;
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA gov TO veyrqun_app;
        GRANT SELECT, INSERT, UPDATE ON iam.workspaces, iam.signing_keys TO veyrqun_app;
        GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA audit TO veyrqun_app;
        GRANT USAGE ON SEQUENCE audit.events_seq TO veyrqun_app;
        GRANT EXECUTE ON FUNCTION audit.ensure_partitions(integer) TO veyrqun_app;
    END IF;
END $$;
"""

DOWNGRADE = r"""
DROP SCHEMA IF EXISTS audit CASCADE;
DROP SCHEMA IF EXISTS gov CASCADE;
DROP SCHEMA IF EXISTS iam CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
