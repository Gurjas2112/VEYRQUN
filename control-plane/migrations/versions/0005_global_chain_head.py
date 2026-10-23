"""The audit chain is global, but appends run in tenant-scoped transactions under row-level security, so
`SELECT entry_hash ... ORDER BY seq DESC LIMIT 1` returned the caller's own latest entry instead of the global head.
Two workspaces writing in turn forked the chain (caught on the hosted demo by verify_chain).

audit.chain_head() is SECURITY DEFINER (owned by the migration role, which RLS does not apply to) and returns only
the latest hash: no tenant data crosses the boundary.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
CREATE FUNCTION audit.chain_head() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
    SELECT entry_hash FROM audit.events ORDER BY seq DESC LIMIT 1
$$;
REVOKE ALL ON FUNCTION audit.chain_head() FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veyrqun_app') THEN
        GRANT EXECUTE ON FUNCTION audit.chain_head() TO veyrqun_app;
    END IF;
END $$;
""")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS audit.chain_head()")
