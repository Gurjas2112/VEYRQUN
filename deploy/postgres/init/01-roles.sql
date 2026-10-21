-- Dev/test bootstrap only. Separates the migration owner from the runtime app role so the
-- app can be denied UPDATE/DELETE on the audit ledger (Phase 1B).
CREATE ROLE veyrqun_app LOGIN PASSWORD 'veyrqun_app_dev';
GRANT CONNECT ON DATABASE veyrqun TO veyrqun_app;
CREATE EXTENSION IF NOT EXISTS vector;
