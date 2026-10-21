"""Identity, roles, tenancy (row-level security) and data protection.

* iam.users: mirrored from the IdP by `sub`; email stored encrypted (per-user identity key) + HMAC lookup hash.
* iam.identity_keys: per-user data key. Deleting it crypto-shreds the person's identity while the audit chain
  (which only ever records `user:<uuid>`) stays valid.
* iam.memberships: per-workspace roles (VEYRQUN is the authorization source of truth; the IdP only authenticates).
* iam.invites: one-time, email-bound, role-bound, 72 h.
* Row-level security on every tenant table: rows are visible only when
  current_setting('app.workspace_id') equals the row's workspace, or is '*' (system/platform scope).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_TABLES = ["gov.agents", "gov.fleet_state", "gov.approvals", "audit.events", "iam.memberships", "iam.invites"]
SCOPE = "(current_setting('app.workspace_id', true) = '*' OR workspace_id::text = current_setting('app.workspace_id', true))"

UPGRADE = r"""
ALTER TABLE iam.workspaces
    ADD COLUMN environment text NOT NULL DEFAULT 'production'
        CHECK (environment IN ('production', 'staging', 'sandbox')),
    ADD COLUMN owner_user_id uuid,
    ADD COLUMN quota_agents integer,
    ADD COLUMN quota_requests_per_day integer;

CREATE TABLE iam.users (
    id             uuid PRIMARY KEY,
    idp_issuer     text NOT NULL,
    idp_sub        text NOT NULL,
    email_hash     text NOT NULL,
    email_enc      bytea,
    display_enc    bytea,
    status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'erased')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz,
    UNIQUE (idp_issuer, idp_sub)
);
CREATE INDEX users_email_hash ON iam.users (email_hash);

CREATE TABLE iam.identity_keys (
    user_id     uuid PRIMARY KEY REFERENCES iam.users(id) ON DELETE CASCADE,
    key_enc     bytea,
    shredded_at timestamptz
);

CREATE TABLE iam.memberships (
    workspace_id uuid NOT NULL REFERENCES iam.workspaces(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
    role         text NOT NULL CHECK (role IN ('platform_admin', 'security_operator', 'approver', 'auditor',
                                               'app_owner', 'viewer')),
    granted_by   text NOT NULL,
    granted_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id, role)
);

CREATE TABLE iam.invites (
    id           uuid PRIMARY KEY,
    workspace_id uuid NOT NULL REFERENCES iam.workspaces(id) ON DELETE CASCADE,
    email_hash   text NOT NULL,
    email_enc    bytea NOT NULL,
    role         text NOT NULL,
    token_hash   text NOT NULL UNIQUE,
    created_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    accepted_at  timestamptz,
    accepted_by  uuid,
    revoked_at   timestamptz
);

ALTER TABLE gov.agents ADD COLUMN owner_user_id uuid;
"""


def _rls() -> str:
    stmts = []
    for table in TENANT_TABLES:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        stmts.append(f"CREATE POLICY tenant_isolation ON {table} USING {SCOPE} WITH CHECK {SCOPE};")
    # Child tables without workspace_id inherit isolation through their agent.
    for table in ("gov.policy_versions", "gov.spend_snapshots"):
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        stmts.append(
            f"CREATE POLICY tenant_isolation ON {table} USING "
            f"(EXISTS (SELECT 1 FROM gov.agents a WHERE a.id = agent_id)) "
            f"WITH CHECK (EXISTS (SELECT 1 FROM gov.agents a WHERE a.id = agent_id));"
        )
    return "\n".join(stmts)


GRANTS = r"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veyrqun_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON iam.users, iam.identity_keys, iam.memberships, iam.invites
            TO veyrqun_app;
        GRANT DELETE ON iam.workspaces TO veyrqun_app;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    op.execute(_rls())
    op.execute(GRANTS)


def downgrade() -> None:
    for table in TENANT_TABLES + ["gov.policy_versions", "gov.spend_snapshots"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}; ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.execute("""
        ALTER TABLE gov.agents DROP COLUMN owner_user_id;
        DROP TABLE iam.invites, iam.memberships, iam.identity_keys, iam.users;
        ALTER TABLE iam.workspaces DROP COLUMN environment, DROP COLUMN owner_user_id,
            DROP COLUMN quota_agents, DROP COLUMN quota_requests_per_day;
    """)
