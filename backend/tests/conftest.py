import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["VEYRQUN_SIMULATOR"] = "0"

from fastapi.testclient import TestClient  # noqa: E402
from veyrqun import service  # noqa: E402
from veyrqun.db import init_db  # noqa: E402
from veyrqun.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("VEYRQUN_DB_FILE", str(tmp_path / "test.db"))
    service.reset_runtime_state()
    init_db()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def base_agent():
    return {
        "agent_id": "test_agent",
        "display_name": "Test Agent",
        "allowed_actions": ["pay", "refund"],
        "blocked_actions": ["wire_transfer"],
        "spend_cap_daily": 1000.0,
        "spend_used_today": 0.0,
        "max_per_transaction": None,
        "rate_limit_per_minute": None,
        "action_limits": {},
        "status": "active",
        "policy_version": 1,
    }
