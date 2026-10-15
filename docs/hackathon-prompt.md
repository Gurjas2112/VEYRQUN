# How to use this

Copy everything below the divider into a fresh session with your coding assistant (Claude Code, Cursor, a new Claude chat with code execution — any of them work). It's written as a direct brief *to* that assistant, so it's self-contained even if that assistant has no memory of how you got here.

Two moments where you stay in the loop:
1. **After Phase 0** (the plan) — review before any code gets written.
2. **Before Phase 6** (the stretch goal) — decide if you have time for it.

Everything else can run hands-off if you're short on time.

---

# Project Brief: Governance Layer for Financial Agents — Solo Hackathon Build

## Role & context

You are my technical partner for a solo hackathon build. I have roughly 16 hours of build time before I need a working, demoable prototype. There is no second developer — you're doing the heavy lifting on code; I'm directing scope, testing on the side, and owning the pitch.

**The competition:** CodeStreet, a hackathon sponsored by American Express for final-year engineering students. Judges are American Express product and engineering leaders. I need a working, demoable prototype — whether that ends up supporting my idea-submission video or the in-person Chennai finale, the same build serves both, so build it to actually run live, not just look good in screenshots.

**The theme I picked:** Governance Layer for Financial Agents — deliberately, because it's the least crowded of the seven themes on offer and the most technically interesting.

## The theme, condensed

Banks are starting to deploy fleets of autonomous AI agents to handle financial tasks. Nobody has good infrastructure to govern them responsibly. The brief asks for: per-agent permissions, real-time spend caps, instant revocation, a full and auditable action log, and a fleet-wide emergency stop.

## Strategic framing — read this before anything else

This is a control tower, not the planes. I am **not** building real AI agents. I'm building the infrastructure that would govern a fleet of agents *if they existed* — permissions, spend caps, revocation, an emergency stop, and an immutable audit trail. The "agents" in this demo are simulated: lightweight scripts that attempt actions so the governance layer has something to govern, monitor, and occasionally block.

> **Elevator pitch, keep this in mind while making judgment calls:** "Every major financial institution is racing to deploy autonomous AI agents. Almost none of them have the infrastructure to govern that fleet responsibly. This is the control layer that makes trustworthy agent deployment possible — permissions, spend caps, a kill switch, and an audit trail you can mathematically verify."

## Scope: build exactly this, nothing more

1. **Permission model** — per-agent allow-list/block-list of actions, plus a daily spend cap
2. **Agent activity simulator** — 5 fake agents attempting actions on a loop
3. **Policy engine** — evaluates every attempted action against live policy in real time
4. **Real-time dashboard** — agent status tiles, live action feed, policy editor, emergency stop
5. **Immutable audit log** — hash-chained, append-only, every decision logged with its reasoning
6. **Emergency stop** — single-agent or whole-fleet kill switch
7. **Demo control panel** — manual buttons that force each of the 3 key demo moments on command (see "Demo narrative" below), running *alongside* the background random simulator

## Scope: explicitly excluded — do not build, do not suggest, do not gold-plate

- Real OPA/Cedar or any policy-as-code framework — write the rules engine in plain Python
- Kafka, Prometheus, Grafana, or anything requiring an external service
- Real LLM-based agents — simulated actions only, no API keys, no external calls of any kind (this also means zero risk of conference wifi killing the demo)
- User authentication, login, or multi-tenant anything
- Mobile responsiveness — desktop browser only, judges will be looking at a laptop
- Docker — keep local setup to `pip install` + `npm install`, two terminals, nothing else
- A formal automated test suite — basic input validation is enough; we don't have time and it isn't part of the demo

If I ask for something on this exclusion list mid-build, remind me it's out of scope before doing it. I'm the one at risk of scope creep at 2am, not you.

## Tech stack — locked

- **Backend:** Python, FastAPI, SQLite, asyncio for the simulator loop, FastAPI's native WebSocket support for the live feed
- **Frontend:** React (Vite), Tailwind CSS — no component library needed
- No Redis, no message queue, no Docker — SQLite plus in-memory state is enough for a single-machine demo

## Data models

Use these exact shapes. Only change field names if there's a strong technical reason — and tell me why if you do.

```python
# Agent
{
    "agent_id": "travel_agent_01",
    "display_name": "Travel Rebooking Agent",
    "allowed_actions": ["flight_rebook", "hotel_rebook"],
    "blocked_actions": ["credit_limit_increase"],
    "spend_cap_daily": 1500.00,
    "spend_used_today": 0.00,   # "daily" just means "since last reset" — see note below
    "status": "active",         # active | stopped
}

# Policy version (for the Phase 6 stretch goal)
{
    "policy_id": "uuid",
    "agent_id": "travel_agent_01",
    "version": 3,
    "created_at": "iso8601",
    "allowed_actions": [...],
    "blocked_actions": [...],
    "spend_cap_daily": 1500.00,
    "active": True,
}

# Action attempt / audit log entry
{
    "action_id": "uuid",
    "agent_id": "travel_agent_01",
    "action_type": "flight_rebook",
    "amount": 1200.00,
    "timestamp": "iso8601",
    "outcome": "approved",        # approved | blocked
    "rule_triggered": None,       # or "spend_cap_exceeded" | "action_not_permitted" | "action_not_in_allowlist" | "emergency_stop_active"
    "policy_version": 3,
    "prev_hash": "sha256 of the previous log entry",
    "entry_hash": "sha256 of this entry's contents + prev_hash",
}
```

**Don't build real midnight-rollover logic for spend caps.** "Daily" can just mean "since last reset" — add a manual `reset-spend` endpoint for demo purposes and move on. This is a classic hackathon time-sink for a feature judges will never actually see exercised live.

**The hash chain is the single most important technical detail in this whole build.** It turns "we log everything" into "we log everything and you can mathematically prove nothing was altered." Every entry chains to the one before it: `entry_hash = SHA-256(json.dumps(entry_fields_except_entry_hash, sort_keys=True))`. Expose a verify endpoint that walks the whole chain and confirms it's unbroken. This is the most impressive 30 seconds of the demo for any technically-minded judge.

## Policy engine — exact evaluation order

```python
def evaluate(agent, action_type, amount):
    if agent.status == "stopped":
        return BLOCKED, "emergency_stop_active"
    if action_type in agent.blocked_actions:
        return BLOCKED, "action_not_permitted"
    if action_type not in agent.allowed_actions:
        return BLOCKED, "action_not_in_allowlist"
    if agent.spend_used_today + amount > agent.spend_cap_daily:
        return BLOCKED, "spend_cap_exceeded"
    return APPROVED, None
```

Every evaluation writes an audit log entry, regardless of outcome.

## API surface

- `POST /agents` — create an agent
- `GET /agents` — list agents with live status and today's spend
- `PATCH /agents/{id}/policy` — update an agent's policy (bumps the version number)
- `POST /agents/{id}/reset-spend` — manual reset for demo purposes
- `POST /simulate/action` — simulator (or demo panel) attempts an action; runs through the policy engine
- `GET /audit-log?agent_id=&outcome=&limit=` — paginated, filterable
- `GET /audit-log/verify` — walks the hash chain, returns `{"valid": true}` or `{"valid": false, "broken_at": "<action_id>"}`
- `POST /emergency-stop` — stop every agent
- `POST /emergency-stop/{agent_id}` — stop one agent
- `POST /resume/{agent_id}` — resume a stopped agent
- `WS /ws/live-feed` — pushes every new audit log entry the instant it's written

## Agent simulator behavior

- 5 agents with distinct personas — travel rebooking, fee reversal, credit limit change, card replacement, balance check — each with different permissions and caps, so blocks happen for different reasons, not just "one agent that's always broken"
- Background loop: every 2–4 seconds, pick a random agent, a plausible action for its persona, and a plausible amount; call `/simulate/action`
- Bias outcomes roughly 70% approved / 30% blocked — enough incident to be interesting without looking broken
- The loop must be pausable so it doesn't fight with manually triggered demo moments

## Demo narrative — everything above exists to serve these 3 moments

1. **Approved, in policy** — an action inside its cap and permissions goes through instantly. *"The system is invisible when everything's fine."*
2. **Blocked, over cap or unpermitted** — an action outside policy gets blocked in milliseconds with a visible reason. *"The moment something's wrong, it's caught."*
3. **Emergency stop** — hit the button, every agent tile flips red at once, the live feed shows a wall of `emergency_stop_active` blocks. *"And we can always pull the plug."*

Then: open the audit log, filter to one agent, hit "Verify chain," watch it come back clean. *"And every one of those decisions is provable after the fact."*

The demo control panel needs one button per moment, plus a "resume all," so none of this is left to the random simulator's luck while judges are watching.

## Execution protocol — work in this order

### Phase 0 — Plan (before writing any code)
Output: the file/folder structure, confirmation of the data models above (or proposed changes with reasoning), the full API route list, and any open questions.
**Stop here and wait for me to say "approved"** before writing code — unless I've said "autopilot," in which case use your own best judgment on open questions and proceed.

### Phase 1 — Backend core (~2–3 hrs)
SQLite schema and models, the policy engine module, the hash-chained audit log writer, all REST endpoints except the WebSocket.
**Done when:** I can `curl` every endpoint and get sane JSON back, including a blocked action and a verified audit chain.

### Phase 2 — Agent simulator (~2 hrs)
The 5 agent personas, the asyncio background loop, pause/resume control.
**Done when:** left running for 60 seconds, the audit log fills with a believable mix of approvals and blocks, across different agents and different reasons.

### Phase 3 — WebSocket live feed (~2 hrs)
Broadcast every new audit log entry to connected clients the instant it's written.
**Done when:** a basic WebSocket test (or the browser console) shows entries streaming in real time while the simulator runs.

### Phase 4 — Frontend dashboard (~4 hrs)
Agent tiles color-coded by status, a live scrolling action feed color-coded by outcome, a policy editor panel per agent, an emergency stop button (fleet-wide and per-agent).
**Done when:** I can watch the simulator's output live in the browser with zero manual refresh, and changing a policy visibly changes future outcomes.

### Phase 5 — Demo control panel + audit log UI (~2 hrs)
The 4 manual trigger buttons from the demo narrative, plus a filterable audit log table with a "Verify chain" button that calls `/audit-log/verify` and shows the result clearly.
**Done when:** I can run the entire 3-moment demo narrative on command, in order, without touching the keyboard except to click buttons.

### Phase 6 — Stretch: policy versioning UI (~1 hr, only if time allows)
Show policy version history per agent; let me roll back to a previous version.
**Skip without guilt if we're short on time** — the hash chain is the impressive technical detail; this is a nice-to-have on top of it.

### Phase 7 — Polish + seed data (~2 hrs)
Seed the database with ~15 minutes of pre-existing history on startup so the dashboard never opens empty. Visual polish pass. Write a literal, second-by-second demo script for the pitch — what I click, what I say, in order — based on the 3 moments above.
**Done when:** I could hand this laptop to a stranger and they could run the whole demo from a printed script.

## Working agreement for execution

- After each phase, give me a one-paragraph status update and the exact command(s) to test what you just built — not just "done."
- Keep the codebase split by concern (models, policy engine, routes, simulator, websocket handler; React components split sensibly). No 1,000-line files.
- If you hit a genuine ambiguity that changes the architecture, ask me one direct question. For anything else, make the sensible hackathon-pragmatic call yourself and tell me what you assumed.
- Comment sparingly — only where the *why* isn't obvious from the code itself.
- If we fall behind schedule, tell me explicitly which phase to cut (Phase 6 first, then trim Phase 5's extras) rather than silently under-building Phases 1–4.

Start with Phase 0.
