"""Background agent-fleet simulator — gives the governance layer something to govern."""

import asyncio
import logging
import random
from decimal import Decimal

from . import service
from .ws import ws_manager

log = logging.getLogger("veyrqun.simulator")

ACTION_POOL = {
    "travel_agent_01": [("flight_rebook", 450.0), ("hotel_rebook", 300.0), ("credit_limit_increase", 1000.0)],
    "fee_reversal_02": [("waive_fee", 35.0), ("account_summary", 0.0), ("wire_transfer", 250.0)],
    "credit_limit_03": [("credit_limit_increase", 2000.0), ("score_check", 0.0), ("card_issue", 50.0)],
    "card_replacement_04": [("card_issue", 25.0), ("card_lock", 0.0), ("waive_fee", 50.0)],
    "balance_check_05": [("account_summary", 0.0), ("balance_check", 0.0), ("wire_transfer", 500.0)],
}

state = {"running": True}


async def run() -> None:
    await asyncio.sleep(2)
    while True:
        await asyncio.sleep(random.uniform(2.0, 4.0))
        if not state["running"]:
            continue
        agent_id = random.choice(list(ACTION_POOL))
        action_type, amount = random.choice(ACTION_POOL[agent_id])
        value = Decimal("0.00")
        if amount > 0:
            value = (Decimal(str(amount)) * Decimal(str(random.uniform(0.8, 1.5)))).quantize(Decimal("0.01"))
        try:
            entry, agent, _ = await asyncio.to_thread(service.attempt_action, agent_id, action_type, value)
            await ws_manager.publish(entry, [agent] if agent else None)
        except service.GovernanceError:
            # Agent was deleted from the pool's perspective; skip silently.
            continue
        except Exception:
            log.exception("simulator tick failed")
