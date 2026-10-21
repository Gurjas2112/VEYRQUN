# VEYRQUN — Documentation Home

> Governance layer for fleets of autonomous financial AI agents.
> Built for the American Express CodeStreet hackathon theme **"Governance Layer for Financial Agents"**.

VEYRQUN is a **control tower, not the planes**: it doesn't build agents, it governs them. Every action an agent
attempts is intercepted, evaluated against that agent's live policy, logged to a tamper-evident hash chain, and
streamed to an operator dashboard — with per-agent revocation and a fleet-wide emergency stop.

## Pages

| Page | What's inside |
|---|---|
| [Architecture](Architecture.md) | Components, request flow, concurrency model |
| [Backend](Backend.md) | Package layout, policy engine, service layer, audit ledger, metrics |
| [Frontend](Frontend.md) | Dashboard components, live feed, operator workflows |
| [API Reference](API%20Reference.md) | Every REST endpoint + the WebSocket feed |
| [Data Models](Data%20Models.md) | Tables, JSON shapes, rule identifiers |
| [Testing](Testing.md) | Test suite, accuracy & latency results, benchmark |
| [Deployment](Deployment.md) | Local, Docker, cloud; environment variables |
| [Demo Script](Demo%20Script.md) | 5-minute judge walkthrough |
| [Technical Debt](Technical%20Debt.md) | Known limits and the production roadmap |
| [hackathon-prompt.md](hackathon-prompt.md) | Original build brief (historical) |

## Problem-statement coverage

| PS task | Where it lives |
|---|---|
| Granular, configurable per-agent permission model | `policy_engine.py`, `PATCH /agents/{id}/policy`, Policy editor |
| Dynamic spend caps enforced in real time | daily cap + per-transaction + per-action caps + rate limits, atomically enforced in `service.attempt_action` |
| Revocation + emergency stop (one agent or whole fleet) | `/agents/{id}/revoke`, `/emergency-stop/{id}`, `/emergency-stop` |
| Operator dashboard (policies, activity, audit) | `frontend/src/` |
| Test & optimise accuracy, latency, auditability | `control-plane/tests/` (45 tests), `control-plane/benchmark.py`, `/metrics` |
