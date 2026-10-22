"""Integration test harness: real PostgreSQL + Redis (docker compose locally, service containers in CI).

A throwaway database `veyrqun_test` is created and migrated once per session (as the owner);
the app under test connects as the restricted runtime role `veyrqun_app`, exactly as in production.
Browsers are simulated over https://testserver so `Secure` / `__Host-` cookies behave as in production.
"""

import os
import sys
import time
import uuid
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
    DEFAULT_WORKSPACE_ENV="staging",   # direct publishes; production maker-checker has its own tests
    OIDC_ISSUER="https://idp.test/realms/veyrqun",
    OIDC_CLIENT_SECRET="test-client-secret",
    OIDC_REDIRECT_URI="https://testserver/auth/callback",
    APP_BASE_URL="https://app.test",
    ALLOWED_ORIGINS="https://app.test",
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from fake_idp import ISSUER, FakeIdP  # noqa: E402
from veyrqun import counters, service  # noqa: E402
from veyrqun.auth import directory, oidc, sessions  # noqa: E402
from veyrqun.auth.deps import cookie_names  # noqa: E402
from veyrqun.main import app  # noqa: E402

CONTROL_PLANE = Path(__file__).resolve().parents[1]
DEFAULT_ROLES = ("security_operator", "app_owner", "approver")


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
            "TRUNCATE audit.events, audit.decision_ids, audit.anchors, gov.approvals, gov.spend_snapshots, "
            "gov.policy_versions, gov.agents, gov.fleet_state, gov.bundle_revisions, iam.signing_keys, iam.memberships, "
            "iam.invites, iam.identity_keys, iam.users, iam.workspaces, audit.legacy_v2_events, migration.id_map, "
            "migration.runs RESTART IDENTITY CASCADE"
        ))
        conn.execute(text("ALTER SEQUENCE audit.events_seq RESTART WITH 1"))
    counters.client().flushdb()
    service.reset_runtime_state()
    service.default_workspace()
    yield


@pytest.fixture
def idp():
    fake = FakeIdP()
    oidc.set_provider(oidc.Provider(transport=fake.transport()))
    yield fake
    oidc.set_provider(None)


def make_user(roles=DEFAULT_ROLES, *, workspace_id=None, email=None) -> uuid.UUID:
    ws = workspace_id or service.default_workspace()
    user_id = directory.upsert_user(ISSUER, f"sub-{uuid.uuid4()}", email or f"{uuid.uuid4().hex[:8]}@bank.test", "Test")
    for role in roles:
        directory.grant_role(ws, user_id, role, "test:setup")
    return user_id


def login_as(client: TestClient, user_id: uuid.UUID, *, workspace_id=None, mfa: bool = True,
             auth_time: int | None = None) -> TestClient:
    ws = str(workspace_id or service.default_workspace())
    roles = sorted(directory.roles_in(user_id, uuid.UUID(ws)))
    s = sessions.Session(user_id=str(user_id), issuer=ISSUER, sub="test", workspace_id=ws, roles=roles, mfa=mfa,
                         auth_time=auth_time if auth_time is not None else int(time.time()))
    session_id = sessions.create(s)
    session_cookie, csrf_cookie = cookie_names()
    client.cookies.set(session_cookie, session_id, domain="testserver.local")
    client.cookies.set(csrf_cookie, s.csrf, domain="testserver.local")
    client.headers.update({"X-CSRF-Token": s.csrf, "Origin": "https://app.test"})
    return client


def new_client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def anon_client():
    with new_client() as c:
        yield c


@pytest.fixture
def client():
    """An authenticated operator (security_operator + app_owner + approver, MFA, fresh login)."""
    with new_client() as c:
        yield login_as(c, make_user())


@pytest.fixture
def ws():
    return service.default_workspace()
