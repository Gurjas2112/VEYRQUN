"""Builds the policy *data* document (agents + limits + fleet state) for a workspace and keeps
it cached by bundle revision. The same document ships to gateways inside the signed bundle."""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text

from ..db import tx
from ..money import to_minor

DEFAULT_SETTINGS = {
    "injection_block_threshold": 0.9,
    # Read-only, zero-amount actions that may proceed if the counter store is unreachable.
    "fail_open_safe_actions": ["account_summary", "balance_check", "score_check"],
    # MCP tool -> JSON pointer to the money amount inside the tool's arguments (gateway uses this).
    "tool_amounts": {
        "waive_fee": "/amount", "flight_rebook": "/fare", "hotel_rebook": "/amount", "card_issue": "/fee",
        "credit_limit_increase": "/increase", "wire_transfer": "/amount",
    },
}

_AGENT_POLICY_SQL = """
SELECT a.id, a.agent_key, a.display_name, a.status, a.status_reason, a.mode, a.timezone, a.currency,
       a.accountable_owner, a.business_service, a.policy_version, a.created_at, a.owner_user_id,
       p.allowed_actions, p.blocked_actions, p.spend_cap_daily, p.max_per_transaction, p.action_limits,
       p.action_daily_caps, p.rate_limit_per_minute, p.approval_threshold, p.redact_data_classes,
       p.block_data_classes
FROM gov.agents a
JOIN gov.policy_versions p ON p.agent_id = a.id AND p.version = a.policy_version
WHERE a.workspace_id = :ws
ORDER BY a.created_at, a.id
"""


def _minor_map(raw: dict | None, currency: str) -> dict[str, int]:
    return {k: to_minor(Decimal(str(v)), currency) for k, v in (raw or {}).items()}


def agent_policy_doc(row) -> dict:
    """Policy-engine view of one agent: integer minor units only, no floats."""
    cur = row.currency
    return {
        "status": row.status,
        "mode": row.mode,
        "policy_version": row.policy_version,
        "allowed_actions": list(row.allowed_actions),
        "blocked_actions": list(row.blocked_actions),
        "spend_cap_daily_minor": to_minor(row.spend_cap_daily, cur),
        "max_per_transaction_minor": None if row.max_per_transaction is None else to_minor(row.max_per_transaction, cur),
        "action_limits_minor": _minor_map(row.action_limits, cur),
        "action_daily_caps_minor": _minor_map(row.action_daily_caps, cur),
        "rate_limit_per_minute": row.rate_limit_per_minute,
        "approval_threshold_minor": None if row.approval_threshold is None else to_minor(row.approval_threshold, cur),
        "redact_data_classes": list(row.redact_data_classes),
        "block_data_classes": list(row.block_data_classes),
        "currency": cur,
        "timezone": row.timezone,
    }


def load_workspace(workspace_id: UUID) -> tuple[int, dict, list]:
    with tx(workspace_id) as conn:
        revision = conn.execute(text("SELECT coalesce(max(revision), 0) FROM gov.bundle_revisions")).scalar_one()
        rows = conn.execute(text(_AGENT_POLICY_SQL), {"ws": workspace_id}).all()
        fleet = conn.execute(
            text("SELECT stopped, version FROM gov.fleet_state WHERE workspace_id = :ws"), {"ws": workspace_id}
        ).first()
    data = {
        "agents": {r.agent_key: agent_policy_doc(r) for r in rows},
        "fleet": {"stopped": bool(fleet.stopped) if fleet else False, "version": fleet.version if fleet else 0},
        "settings": DEFAULT_SETTINGS,
        "revision": revision,
    }
    return revision, data, rows


class PolicyStore:
    """Process-local cache keyed by workspace; refreshed when the global bundle revision moves."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[UUID, tuple[int, dict, list]] = {}

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, workspace_id: UUID, current_revision: int | None) -> tuple[int, dict, list]:
        with self._lock:
            hit = self._cache.get(workspace_id)
        if hit is not None and current_revision is not None and hit[0] == current_revision:
            return hit
        fresh = load_workspace(workspace_id)
        with self._lock:
            self._cache[workspace_id] = fresh
        return fresh


def canonical_data_json(data: dict) -> bytes:
    return json.dumps({"policy_data": data}, sort_keys=True, separators=(",", ":")).encode()


store = PolicyStore()
