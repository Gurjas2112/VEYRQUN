"""PostgreSQL access (SQLAlchemy 2 Core + psycopg 3) and seed data."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, text

from .config import get_settings
from .ids import uuid7

SEED_WORKSPACE = {"slug": "default", "name": "Default workspace"}

SEED_AGENTS = [
    {
        "agent_key": "travel_agent_01",
        "display_name": "Travel Rebooking Agent",
        "allowed_actions": ["flight_rebook", "hotel_rebook"],
        "blocked_actions": ["credit_limit_increase"],
        "spend_cap_daily": Decimal("1500.00"),
        "max_per_transaction": Decimal("600.00"),
        "rate_limit_per_minute": 10,
        "action_limits": {},
        "business_service": "Travel servicing",
    },
    {
        "agent_key": "fee_reversal_02",
        "display_name": "Fee Reversal Agent",
        "allowed_actions": ["waive_fee", "account_summary"],
        "blocked_actions": ["wire_transfer"],
        "spend_cap_daily": Decimal("500.00"),
        "max_per_transaction": Decimal("100.00"),
        "rate_limit_per_minute": 10,
        "action_limits": {"waive_fee": Decimal("50.00")},
        "business_service": "Card fees & disputes",
        "redact_data_classes": ["email", "phone"],
    },
    {
        "agent_key": "credit_limit_03",
        "display_name": "Credit Limit Agent",
        "allowed_actions": ["credit_limit_increase", "score_check"],
        "blocked_actions": ["card_issue"],
        "spend_cap_daily": Decimal("5000.00"),
        "max_per_transaction": Decimal("2500.00"),
        "rate_limit_per_minute": 6,
        "action_limits": {},
        "approval_threshold": Decimal("2000.00"),
        "business_service": "Credit decisioning",
        "block_data_classes": ["pci_pan"],
    },
    {
        "agent_key": "card_replacement_04",
        "display_name": "Card Replacement Agent",
        "allowed_actions": ["card_issue", "card_lock"],
        "blocked_actions": ["waive_fee"],
        "spend_cap_daily": Decimal("200.00"),
        "max_per_transaction": Decimal("40.00"),
        "rate_limit_per_minute": 10,
        "action_limits": {},
        "business_service": "Card servicing",
    },
    {
        "agent_key": "balance_check_05",
        "display_name": "Account Inquiry Agent",
        "allowed_actions": ["account_summary", "balance_check"],
        "blocked_actions": ["wire_transfer", "flight_rebook"],
        "spend_cap_daily": Decimal("100.00"),
        "max_per_transaction": None,
        "rate_limit_per_minute": 4,
        "action_limits": {},
        "business_service": "Customer servicing",
    },
]


@lru_cache(maxsize=8)
def _engine(url: str) -> Engine:
    # pool_pre_ping survives Postgres restarts and serverless idle disconnects.
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, future=True)


def engine() -> Engine:
    return _engine(get_settings().database_url)


@contextmanager
def tx(workspace_id: UUID | None = None) -> Iterator[Connection]:
    """One transaction. With a workspace id, row-level security is scoped to it (Phase 1B)."""
    with engine().begin() as conn:
        if workspace_id is not None:
            conn.execute(text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(workspace_id)})
        yield conn


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _jsonable_limits(limits: dict) -> str:
    return json.dumps({k: str(v) for k, v in (limits or {}).items()}, sort_keys=True)


def insert_policy_version(conn: Connection, agent_id: UUID, version: int, policy: dict, created_by: str, note: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO gov.policy_versions
                (id, agent_id, version, created_by, note, allowed_actions, blocked_actions, spend_cap_daily,
                 max_per_transaction, action_limits, action_daily_caps, rate_limit_per_minute, approval_threshold,
                 redact_data_classes, block_data_classes)
            VALUES (:id, :agent_id, :version, :created_by, :note, :allowed, :blocked, :cap, :per_tx,
                    CAST(:action_limits AS jsonb), CAST(:action_daily_caps AS jsonb), :rate, :approval,
                    :redact, :block)
            """
        ),
        {
            "id": uuid7(),
            "agent_id": agent_id,
            "version": version,
            "created_by": created_by,
            "note": note,
            "allowed": list(policy["allowed_actions"]),
            "blocked": list(policy["blocked_actions"]),
            "cap": policy["spend_cap_daily"],
            "per_tx": policy.get("max_per_transaction"),
            "action_limits": _jsonable_limits(policy.get("action_limits")),
            "action_daily_caps": _jsonable_limits(policy.get("action_daily_caps")),
            "rate": policy.get("rate_limit_per_minute"),
            "approval": policy.get("approval_threshold"),
            "redact": list(policy.get("redact_data_classes") or []),
            "block": list(policy.get("block_data_classes") or []),
        },
    )


def insert_agent(conn: Connection, workspace_id: UUID, agent: dict, created_by: str) -> UUID:
    agent_id = uuid7()
    conn.execute(
        text(
            """
            INSERT INTO gov.agents (id, workspace_id, agent_key, display_name, timezone, currency, mode,
                                    accountable_owner, business_service, client_secret_hash)
            VALUES (:id, :ws, :key, :name, :tz, :currency, :mode, :owner, :service, :secret_hash)
            """
        ),
        {
            "id": agent_id,
            "ws": workspace_id,
            "key": agent["agent_key"],
            "name": agent["display_name"],
            "tz": agent.get("timezone", "UTC"),
            "currency": agent.get("currency", "USD"),
            "mode": agent.get("mode", "enforce"),
            "owner": agent.get("accountable_owner"),
            "service": agent.get("business_service"),
            "secret_hash": agent.get("client_secret_hash"),
        },
    )
    insert_policy_version(conn, agent_id, 1, agent, created_by, "initial policy")
    return agent_id


def bump_bundle_revision(conn: Connection, reason: str) -> int:
    return conn.execute(
        text("INSERT INTO gov.bundle_revisions (reason) VALUES (:r) RETURNING revision"), {"r": reason}
    ).scalar_one()


def ensure_seed() -> UUID:
    """Create the default workspace (and demo fleet if it is empty). Idempotent; returns its id."""
    with tx() as conn:
        ws = conn.execute(text("SELECT id FROM iam.workspaces WHERE slug = :s"), {"s": SEED_WORKSPACE["slug"]}).scalar()
        if ws is None:
            ws = uuid7()
            conn.execute(
                text("INSERT INTO iam.workspaces (id, slug, name) VALUES (:id, :slug, :name) ON CONFLICT (slug) DO NOTHING"),
                {"id": ws, **SEED_WORKSPACE},
            )
            ws = conn.execute(text("SELECT id FROM iam.workspaces WHERE slug = :s"), {"s": SEED_WORKSPACE["slug"]}).scalar_one()
        conn.execute(text("INSERT INTO gov.fleet_state (workspace_id) VALUES (:w) ON CONFLICT DO NOTHING"), {"w": ws})
        if not conn.execute(text("SELECT 1 FROM gov.agents WHERE workspace_id = :w LIMIT 1"), {"w": ws}).first():
            for agent in SEED_AGENTS:
                insert_agent(conn, ws, agent, "system:seed")
            bump_bundle_revision(conn, "seed")
        return ws
