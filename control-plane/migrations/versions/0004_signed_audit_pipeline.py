"""Signed audit pipeline: HMAC per entry, hash version 2 (adds `details`), idempotency on decision_id,
and signed head anchors.

* hash_version 1 rows keep verifying with the original field set; new rows use version 2, which also hashes
  `details` (data classes, redactions, payload hashes, timings, approval id, ...).
* hmac / hmac_kid: HMAC-SHA256(entry_hash) under a rotatable secret. Without the secret, an attacker who rewrites
  the chain and recomputes every SHA-256 still can't produce valid HMACs.
* audit.decision_ids: one row per decision_id, so a redelivered stream event never forks the chain.
* audit.anchors: periodically signed (seq, head_hash) checkpoints, append-only, optionally copied to object storage.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = r"""
ALTER TABLE audit.events
    ADD COLUMN details      jsonb    NOT NULL DEFAULT '{}',
    ADD COLUMN hash_version smallint NOT NULL DEFAULT 1,
    ADD COLUMN hmac         text,
    ADD COLUMN hmac_kid     text;

CREATE TABLE audit.decision_ids (
    decision_id  text PRIMARY KEY,
    seq          bigint NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO audit.decision_ids (decision_id, seq)
    SELECT decision_id, min(seq) FROM audit.events GROUP BY decision_id ON CONFLICT DO NOTHING;

CREATE TABLE audit.anchors (
    id           bigserial PRIMARY KEY,
    seq          bigint NOT NULL,
    head_hash    text NOT NULL,
    entries      bigint NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    signature    text NOT NULL,
    kid          text NOT NULL,
    external_ref text
);
CREATE TRIGGER anchors_append_only BEFORE UPDATE OR DELETE ON audit.anchors
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER decision_ids_append_only BEFORE UPDATE OR DELETE ON audit.decision_ids
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

ALTER TABLE iam.signing_keys DROP CONSTRAINT IF EXISTS signing_keys_purpose_check;
ALTER TABLE iam.signing_keys ADD CONSTRAINT signing_keys_purpose_check
    CHECK (purpose IN ('agent_jwt', 'bundle', 'audit_hmac', 'audit_anchor'));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'veyrqun_app') THEN
        GRANT SELECT, INSERT ON audit.decision_ids, audit.anchors TO veyrqun_app;
        GRANT USAGE ON SEQUENCE audit.anchors_id_seq TO veyrqun_app;
    END IF;
END $$;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS audit.anchors;
DROP TABLE IF EXISTS audit.decision_ids;
ALTER TABLE audit.events DROP COLUMN details, DROP COLUMN hash_version, DROP COLUMN hmac, DROP COLUMN hmac_kid;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
