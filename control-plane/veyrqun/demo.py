"""Public, time-limited demo: anonymous guests each get a private sandbox with a ready-made fleet.

Nothing here changes how decisions are made. A guest's agents are governed by the same Rego policy, Redis counters,
gateway and audit chain as any other workspace. What is special (ADR 0014):
* no IdP: a guest session is created by `POST /auth/demo` and is flagged `demo_guest`;
* `demo_guest` sessions may act only inside their own unexpired sandbox, and only there are MFA/step-up waived;
* everything stops at DEMO_EXPIRES_AT: guest login returns 410, guest sessions get 410, and sandbox bundles are
  refused so gateways fail closed once their last good bundle goes stale.
"""

from __future__ import annotations

import secrets
import time
from decimal import Decimal
from uuid import UUID

from .auth import directory, sessions
from .config import get_settings
from .counters import client as redis_client

GUEST_ISSUER = "veyrqun:demo"
GUEST_ROLES = ("security_operator", "app_owner", "approver")

# The guest's fleet. Same shapes as agents/fleet/specs.py (the LangGraph scenarios), so the two demos tell one story.
FLEET: list[dict] = [
    {"agent_id": "fee_reversal_bot", "display_name": "Fee reversal assistant", "accountable_owner": "Retail Servicing",
     "business_service": "card-servicing", "allowed_actions": ["llm.chat", "account_summary", "waive_fee"],
     "blocked_actions": ["wire_transfer"], "spend_cap_daily": Decimal("300.00"), "max_per_transaction": Decimal("100.00"),
     "approval_threshold": Decimal("50.00"), "redact_data_classes": ["email", "phone"], "block_data_classes": ["pci_pan", "secret"]},
    {"agent_id": "card_replacement_bot", "display_name": "Card replacement assistant",
     "accountable_owner": "Card Operations", "business_service": "card-servicing",
     "allowed_actions": ["llm.chat", "card_lock", "card_issue"], "blocked_actions": ["waive_fee"],
     "spend_cap_daily": Decimal("200.00"), "max_per_transaction": Decimal("40.00"), "block_data_classes": ["pci_pan", "secret"]},
    {"agent_id": "travel_bot", "display_name": "Travel disruption assistant", "accountable_owner": "Premium Travel",
     "business_service": "travel", "allowed_actions": ["llm.chat", "flight_rebook", "hotel_rebook"],
     "blocked_actions": ["credit_limit_increase"], "spend_cap_daily": Decimal("1500.00"),
     "max_per_transaction": Decimal("600.00"), "approval_threshold": Decimal("500.00"), "block_data_classes": ["pci_pan", "secret"]},
    {"agent_id": "rogue_bot", "display_name": "Compromised assistant (red team)", "accountable_owner": "Security Red Team",
     "business_service": "red-team", "allowed_actions": ["llm.chat", "account_summary", "waive_fee"],
     "blocked_actions": ["wire_transfer", "credit_limit_increase"], "spend_cap_daily": Decimal("100.00"),
     "max_per_transaction": Decimal("40.00"), "rate_limit_per_minute": 30, "block_data_classes": ["pci_pan", "secret"]},
]


class DemoUnavailable(Exception):
    def __init__(self, status_code: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(detail)
        self.status_code, self.detail, self.retry_after = status_code, detail, retry_after


def status() -> dict:
    s = get_settings()
    return {"enabled": s.demo_mode, "active": s.demo_active(),
            "expires_at": s.demo_expires_at.isoformat() if s.demo_expires_at else None, "sso": s.sso_enabled}


def _rate_limit(ip: str) -> None:
    s = get_settings()
    hour = int(time.time() // 3600)
    for key, limit in ((f"vq:rl:demo:ip:{ip}:{hour}", s.demo_guests_per_ip_per_hour),
                       (f"vq:rl:demo:all:{hour}", s.demo_max_guests_per_hour)):
        n = redis_client().incr(key)
        if n == 1:
            redis_client().expire(key, 3700)
        if n > limit:
            raise DemoUnavailable(429, "too many demo sandboxes right now, try again later", retry_after=3600)


def create_guest(ip: str) -> tuple[str, sessions.Session, UUID]:
    """A new anonymous guest with a seeded sandbox. Returns (session id, session, workspace id)."""
    from . import service

    s = get_settings()
    if not s.demo_mode:
        raise DemoUnavailable(404, "the public demo is not enabled on this deployment")
    if not s.demo_active():
        raise DemoUnavailable(410, "the public demo has ended")
    _rate_limit(ip)
    handle = secrets.token_hex(6)
    user_id = directory.upsert_user(GUEST_ISSUER, handle, f"guest-{handle}@demo.invalid", "Demo guest")
    ws = directory.create_sandbox(user_id, "", roles=GUEST_ROLES, expires_at=s.demo_expires_at,
                                  actor="system:demo")
    for spec in FLEET:
        service.create_agent({**spec, "owner_user_id": str(user_id)}, actor="system:demo-seed", workspace_id=ws)
    session = sessions.Session(user_id=str(user_id), issuer=GUEST_ISSUER, sub=handle, workspace_id=str(ws),
                               roles=list(GUEST_ROLES), mfa=False, auth_time=int(time.time()), demo_guest=True)
    return sessions.create(session), session, ws
