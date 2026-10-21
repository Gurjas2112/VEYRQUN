"""Alembic runs as the schema owner (MIGRATION_DATABASE_URL), never as the runtime app role."""

from alembic import context
from sqlalchemy import create_engine, pool

from veyrqun.config import get_settings

config = context.config


def run_migrations_online() -> None:
    settings = get_settings()
    url = config.attributes.get("url") or settings.migration_database_url or settings.database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
