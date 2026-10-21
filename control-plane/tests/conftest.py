"""Integration test harness: real PostgreSQL + Redis (docker compose locally, service containers in CI).

A throwaway database `veyrqun_test` is created and migrated once per session (as the owner);
the app under test connects as the restricted runtime role `veyrqun_app`, exactly as in production.
"""

import os
import sys
from pathlib import Path

import pytest

PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
PG_PORT = os.environ.get("TEST_PG_PORT", "55432")
OWNER = os.environ.get("TEST_PG_OWNER", "veyrqun_owner:veyrqun_owner_dev")
APP = os.environ.get("TEST_PG_APP", "veyrqun_app:veyrqun_app_dev")
TEST_DB = "veyrqun_test"
REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")

OWNER_URL = f"postgresql+psycopg://{OWNER}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
os.environ.update(
    VEYRQUN_ENV="test",
    VEYRQUN_SIMULATOR="0",
    DATABASE_URL=f"postgresql+psycopg://{APP}@{PG_HOST}:{PG_PORT}/{TEST_DB}",
    MIGRATION_DATABASE_URL=OWNER_URL,
    REDIS_URL=REDIS,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from veyrqun import counters, service  # noqa: E402
from veyrqun.main import app  # noqa: E402

CONTROL_PLANE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def database():
    admin = create_engine(f"postgresql+psycopg://{OWNER}@{PG_HOST}:{PG_PORT}/postgres",
                          isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
        conn.execute(text(f"GRANT CONNECT ON DATABASE {TEST_DB} TO veyrqun_app"))
    cfg = Config(str(CONTROL_PLANE / "alembic.ini"))
    cfg.attributes["url"] = OWNER_URL
    command.upgrade(cfg, "head")
    yield
    admin.dispose()


@pytest.fixture(scope="session")
def owner_engine(database):
    eng = create_engine(OWNER_URL, poolclass=NullPool)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean_state(owner_engine):
    with owner_engine.begin() as conn:
        # TRUNCATE doesn't fire the row-level append-only trigger; only the owner may do it.
        conn.execute(text(
            "TRUNCATE audit.events, gov.approvals, gov.spend_snapshots, gov.policy_versions, gov.agents, "
            "gov.fleet_state, gov.bundle_revisions, iam.signing_keys, iam.workspaces, audit.legacy_v2_events, "
            "migration.id_map, migration.runs CASCADE"
        ))
        conn.execute(text("ALTER SEQUENCE audit.events_seq RESTART WITH 1"))
    counters.client().flushdb()
    service.reset_runtime_state()
    service.default_workspace()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ws():
    return service.default_workspace()
