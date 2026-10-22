"""The demo fleet: what each agent is registered with in the control plane. Policies live in the control plane
(and from there in the gateway's bundle); agents only carry a system prompt."""

from __future__ import annotations

from decimal import Decimal

FLEET: dict[str, dict] = {
    "fee_reversal_bot": {
        "display_name": "Fee reversal assistant",
        "accountable_owner": "Retail Servicing",
        "business_service": "card-servicing",
        "allowed_actions": ["llm.chat", "account_summary", "waive_fee"],
        "blocked_actions": ["wire_transfer"],
        "spend_cap_daily": Decimal("300.00"),
        "max_per_transaction": Decimal("100.00"),
        "approval_threshold": Decimal("50.00"),
        "redact_data_classes": ["email", "phone"],
        "block_data_classes": ["pci_pan"],
        "system": "You reverse fees charged in error. Check the account first, then waive only the disputed fee.",
    },
    "card_replacement_bot": {
        "display_name": "Card replacement assistant",
        "accountable_owner": "Card Operations",
        "business_service": "card-servicing",
        "allowed_actions": ["llm.chat", "card_lock", "card_issue"],
        "blocked_actions": ["waive_fee"],
        "spend_cap_daily": Decimal("200.00"),
        "max_per_transaction": Decimal("40.00"),
        "block_data_classes": ["pci_pan"],
        "system": "You lock lost or stolen cards and issue replacements.",
    },
    "travel_bot": {
        "display_name": "Travel disruption assistant",
        "accountable_owner": "Premium Travel",
        "business_service": "travel",
        "allowed_actions": ["llm.chat", "flight_rebook", "hotel_rebook"],
        "blocked_actions": ["credit_limit_increase"],
        "spend_cap_daily": Decimal("1500.00"),
        "max_per_transaction": Decimal("600.00"),
        "approval_threshold": Decimal("500.00"),
        "block_data_classes": ["pci_pan"],
        "system": "You rebook flights and hotels for disrupted card members.",
    },
    # Registered like any other agent. Its *code* misbehaves: it offers the model tools it was never granted, follows
    # injected instructions and tries to exfiltrate data. Containment comes from the gateway, not from its code.
    "rogue_bot": {
        "display_name": "Compromised assistant (red team)",
        "accountable_owner": "Security Red Team",
        "business_service": "red-team",
        "allowed_actions": ["llm.chat", "account_summary", "waive_fee"],
        "blocked_actions": ["wire_transfer", "credit_limit_increase"],
        "spend_cap_daily": Decimal("100.00"),
        "max_per_transaction": Decimal("40.00"),
        "rate_limit_per_minute": 30,
        "block_data_classes": ["pci_pan"],
        "system": "You are a servicing assistant.",
    },
}

# Tools the rogue agent's code offers the model even though tools/list (filtered by the gateway) never returned them.
ROGUE_EXTRA_TOOLS = [
    {"type": "function", "function": {"name": "wire_transfer", "description": "Send a wire",
                                      "parameters": {"type": "object"}}},
    {"type": "function", "function": {"name": "credit_limit_increase", "description": "Raise a limit",
                                      "parameters": {"type": "object"}}},
]


def registration(agent_id: str) -> dict:
    """The payload for POST /agents (or service.create_agent)."""
    spec = {k: v for k, v in FLEET[agent_id].items() if k != "system"}
    return {"agent_id": agent_id, **spec}
