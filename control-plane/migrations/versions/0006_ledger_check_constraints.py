"""The database rejects impossible ledger rows, whatever code path wrote them.

Application validation (pydantic, the audit writer's parse_event) is the first line; these constraints are the
last: a negative amount, a non-ISO currency, an unknown outcome or mode, or a hash version the verifier doesn't
know can never be stored. Existing rows are validated when the constraints are added (the migration fails loudly
rather than leaving bad history in place).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CHECKS = {
    "events_amount_non_negative": "amount >= 0",
    "events_currency_iso4217": "currency ~ '^[A-Z]{3}$'",
    "events_outcome_known": "outcome IN ('allow', 'block', 'redact', 'require_approval', 'executed')",
    "events_mode_known": "mode IN ('observe', 'enforce')",
    "events_hash_version_known": "hash_version IN (1, 2)",
    "events_hashes_hex": "entry_hash ~ '^[0-9a-f]{64}$' AND (prev_hash ~ '^[0-9a-f]{64}$' OR prev_hash = 'GENESIS')",
}


def upgrade() -> None:
    for name, expr in CHECKS.items():
        op.execute(f"ALTER TABLE audit.events ADD CONSTRAINT {name} CHECK ({expr})")


def downgrade() -> None:
    for name in CHECKS:
        op.execute(f"ALTER TABLE audit.events DROP CONSTRAINT IF EXISTS {name}")
