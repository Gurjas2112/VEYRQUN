import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from . import config

# Serialises evaluate -> spend update -> audit append so concurrent agents can never
# jointly overshoot a spend cap or fork the hash chain.
write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    allowed_actions TEXT NOT NULL,
    blocked_actions TEXT NOT NULL,
    spend_cap_daily REAL NOT NULL,
    spend_used_today REAL NOT NULL DEFAULT 0.0,
    max_per_transaction REAL,
    rate_limit_per_minute INTEGER,
    action_limits TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    status_reason TEXT,
    policy_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS policy_versions (
    policy_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    allowed_actions TEXT NOT NULL,
    blocked_actions TEXT NOT NULL,
    spend_cap_daily REAL NOT NULL,
    max_per_transaction REAL,
    rate_limit_per_minute INTEGER,
    action_limits TEXT NOT NULL DEFAULT '{}',
    note TEXT,
    UNIQUE (agent_id, version)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    action_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    amount REAL NOT NULL,
    timestamp TEXT NOT NULL,
    outcome TEXT NOT NULL,
    rule_triggered TEXT,
    policy_version INTEGER NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_logs (agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_logs (outcome);

CREATE TABLE IF NOT EXISTS fleet_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SEED_AGENTS = [
    {
        "agent_id": "travel_agent_01",
        "display_name": "Travel Rebooking Agent",
        "allowed_actions": ["flight_rebook", "hotel_rebook"],
        "blocked_actions": ["credit_limit_increase"],
        "spend_cap_daily": 1500.00,
        "max_per_transaction": 600.00,
        "rate_limit_per_minute": 10,
        "action_limits": {},
    },
    {
        "agent_id": "fee_reversal_02",
        "display_name": "Fee Reversal Agent",
        "allowed_actions": ["waive_fee", "account_summary"],
        "blocked_actions": ["wire_transfer"],
        "spend_cap_daily": 500.00,
        "max_per_transaction": 100.00,
        "rate_limit_per_minute": 10,
        "action_limits": {"waive_fee": 50.00},
    },
    {
        "agent_id": "credit_limit_03",
        "display_name": "Credit Limit Agent",
        "allowed_actions": ["credit_limit_increase", "score_check"],
        "blocked_actions": ["card_issue"],
        "spend_cap_daily": 5000.00,
        "max_per_transaction": 2500.00,
        "rate_limit_per_minute": 6,
        "action_limits": {},
    },
    {
        "agent_id": "card_replacement_04",
        "display_name": "Card Replacement Agent",
        "allowed_actions": ["card_issue", "card_lock"],
        "blocked_actions": ["waive_fee"],
        "spend_cap_daily": 200.00,
        "max_per_transaction": 40.00,
        "rate_limit_per_minute": 10,
        "action_limits": {},
    },
    {
        "agent_id": "balance_check_05",
        "display_name": "Account Inquiry Agent",
        "allowed_actions": ["account_summary", "balance_check"],
        "blocked_actions": ["wire_transfer", "flight_rebook"],
        "spend_cap_daily": 100.00,
        "max_per_transaction": None,
        "rate_limit_per_minute": 4,
        "action_limits": {},
    },
]

AGENT_COLUMNS_ADDED_IN_V2 = {
    "max_per_transaction": "REAL",
    "rate_limit_per_minute": "INTEGER",
    "action_limits": "TEXT NOT NULL DEFAULT '{}'",
    "status_reason": "TEXT",
}


class _PooledConnection(sqlite3.Connection):
    """close() hands the connection back instead of closing it, discarding any
    uncommitted work exactly as a real close would."""

    def close(self) -> None:
        self.rollback()


_local = threading.local()


def get_db() -> sqlite3.Connection:
    # Opening a SQLite connection costs ~1 ms on Windows, more than the entire policy
    # decision, so each thread keeps one open connection per database file.
    path = config.db_path()
    pool = _local.__dict__.setdefault("pool", {})
    conn = pool.get(path)
    if conn is None:
        conn = sqlite3.connect(path, timeout=10, factory=_PooledConnection)
        conn.row_factory = sqlite3.Row
        # With WAL, NORMAL stays crash-consistent and skips the fsync on every commit.
        conn.execute("PRAGMA synchronous=NORMAL")
        pool[path] = conn
    return conn


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def row_to_agent(row: sqlite3.Row) -> dict:
    return {
        "agent_id": row["agent_id"],
        "display_name": row["display_name"],
        "allowed_actions": json.loads(row["allowed_actions"]),
        "blocked_actions": json.loads(row["blocked_actions"]),
        "spend_cap_daily": row["spend_cap_daily"],
        "spend_used_today": row["spend_used_today"],
        "max_per_transaction": row["max_per_transaction"],
        "rate_limit_per_minute": row["rate_limit_per_minute"],
        "action_limits": json.loads(row["action_limits"] or "{}"),
        "status": row["status"],
        "status_reason": row["status_reason"],
        "policy_version": row["policy_version"],
    }


def row_to_policy_version(row: sqlite3.Row) -> dict:
    return {
        "policy_id": row["policy_id"],
        "agent_id": row["agent_id"],
        "version": row["version"],
        "created_at": row["created_at"],
        "allowed_actions": json.loads(row["allowed_actions"]),
        "blocked_actions": json.loads(row["blocked_actions"]),
        "spend_cap_daily": row["spend_cap_daily"],
        "max_per_transaction": row["max_per_transaction"],
        "rate_limit_per_minute": row["rate_limit_per_minute"],
        "action_limits": json.loads(row["action_limits"] or "{}"),
        "note": row["note"],
    }


def insert_agent(conn: sqlite3.Connection, agent: dict) -> None:
    conn.execute(
        """
        INSERT INTO agents (agent_id, display_name, allowed_actions, blocked_actions, spend_cap_daily,
                            spend_used_today, max_per_transaction, rate_limit_per_minute, action_limits,
                            status, policy_version)
        VALUES (?, ?, ?, ?, ?, 0.0, ?, ?, ?, 'active', 1)
        """,
        (
            agent["agent_id"],
            agent["display_name"],
            json.dumps(agent["allowed_actions"]),
            json.dumps(agent["blocked_actions"]),
            agent["spend_cap_daily"],
            agent.get("max_per_transaction"),
            agent.get("rate_limit_per_minute"),
            json.dumps(agent.get("action_limits") or {}),
        ),
    )
    insert_policy_version(conn, agent["agent_id"], 1, agent, note="initial policy")


def insert_policy_version(conn: sqlite3.Connection, agent_id: str, version: int, policy: dict, note: str) -> None:
    conn.execute(
        """
        INSERT INTO policy_versions (policy_id, agent_id, version, created_at, allowed_actions, blocked_actions,
                                     spend_cap_daily, max_per_transaction, rate_limit_per_minute, action_limits, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            agent_id,
            version,
            utcnow(),
            json.dumps(policy["allowed_actions"]),
            json.dumps(policy["blocked_actions"]),
            policy["spend_cap_daily"],
            policy.get("max_per_transaction"),
            policy.get("rate_limit_per_minute"),
            json.dumps(policy.get("action_limits") or {}),
            note,
        ),
    )


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(agents)")}
    for column, ddl in AGENT_COLUMNS_ADDED_IN_V2.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE agents ADD COLUMN {column} {ddl}")


def init_db() -> None:
    conn = get_db()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute("INSERT OR IGNORE INTO fleet_state (key, value) VALUES ('fleet_stopped', '0')")
        if conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0:
            for agent in SEED_AGENTS:
                insert_agent(conn, agent)
        conn.commit()
    finally:
        conn.close()
