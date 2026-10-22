# agents/: the demo fleet

LangGraph agents for card servicing and travel, plus a deliberately misbehaving **rogue agent**. They exist to show
that governance holds when the agent code doesn't cooperate.

- **No governance logic in agent code.** Every model call (`/v1/chat/completions`) and tool call (MCP `/mcp`) goes
  through the gateway with a short-lived agent JWT. The agent only routes on the answer: `200` continue, `202`
  wait for a human, anything else stop or tell the model the action was blocked.
- **Human in the loop.** A `202 pending_approval` parks the tool call and the graph pauses with `interrupt()`. The
  run state is in a **Postgres checkpointer**, so the process can exit. After a human decides in the control plane,
  any process resumes the thread. The graph reads the decision back from the control plane rather than trusting the
  resume payload. The retried call carries `x-veyrqun-approval`, which the gateway binds to agent, action and amount.
- **No double execution on resume.** LangGraph re-runs an interrupted node from the top, so the node that interrupts
  has no side effects, and each tool call runs in its own node execution.

## Fleet

| Agent | May do | Limits (from its registration in `fleet/specs.py`) |
|---|---|---|
| `fee_reversal_bot` | `account_summary`, `waive_fee` | $100 per transaction, $300 per day, human approval above $50 |
| `card_replacement_bot` | `card_lock`, `card_issue` | $40 per transaction, $200 per day |
| `travel_bot` | `flight_rebook`, `hotel_rebook` | $600 per transaction, $1,500 per day, approval above $500 |
| `rogue_bot` | `account_summary`, `waive_fee` | $40 per transaction, $100 per day. Its code also offers the model `wire_transfer` and `credit_limit_increase` |

All agents may call `llm.chat`, and all block card numbers (`pci_pan`) in model output.

## Scenarios

`fleet/scenarios.py` declares 12 scenarios, each with the exact sequence of gateway answers it expects (target,
outcome, rule id). Examples: a $75 fee reversal pausing for approval then succeeding; a hijacked model calling a tool
the agent was never granted (`action_not_permitted`); a direct prompt injection (`injection_suspected`); a model
writing a card number into tool arguments (`response:pci_pan`); a spend cap; an operator kill switch.

```bash
docker compose up -d postgres redis
npm --prefix gateway ci
pip install -r requirements-dev.txt          # includes agents/requirements.txt
python scripts/run_fleet_scenarios.py        # writes docs/results/scenarios.json
python -m pytest e2e/test_fleet_e2e.py -q    # the same scenarios as a CI test
```

The model is the deterministic mock (`gateway/src/mock/llm.ts`): `CALL <tool> {json}` in the task makes it emit
that tool call, the way a model follows instructions, or a hijacked model follows injected ones. That keeps CI
offline and the expected traces exact. Pointing the gateway at a real provider changes nothing in the agents.
