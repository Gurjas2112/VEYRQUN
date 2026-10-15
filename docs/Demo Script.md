# Demo Script (~5 minutes)

Recorded walkthrough: [`app_screenshots_screen_recording/veyrqun_demo.mp4`](../app_screenshots_screen_recording/veyrqun_demo.mp4)

## 0. Setup

```bash
python -m uvicorn backend.app:app --port 8000     # terminal 1
cd frontend && npm run dev                         # terminal 2
```

Open `http://localhost:5173`; confirm **Live Feed Online** and the feed is moving.

## 1. Opening (30 s)

> "Banks are racing to deploy autonomous AI agents — rebooking flights, reversing fees, raising credit limits.
> Almost none have the infrastructure to govern that fleet. VEYRQUN is that control layer."

Point at the five agent cards: allow-list, deny-list, daily cap, per-transaction limit, rate limit.

## 2. The system is invisible when things are fine (30 s)

Click **1. Approved Action** → green toast, spend meter moves, entry in the feed.

## 3. Caught in milliseconds (1 min)

* **2. Unpermitted Action** → `action_not_permitted`.
* **3. Spend Cap Breach** → $550 rebookings until `spend_cap_exceeded`.
* Point at **Enforcement Metrics**: blocks by rule, and *policy eval p99 in microseconds* —
  "governance adds well under a millisecond to every action."

## 4. Change the rules live (1 min)

Fee Reversal card → **Policy** → lower the cap, add a note → **Publish**. Version bumps to v2; the next action is
evaluated against v2. **History** → **Roll back to v1** — forward-only, so history is never rewritten.

## 5. Revocation vs. emergency stop (1 min)

* **4. Revoke Agent** → Credit Limit Agent goes violet; its next action → `agent_revoked`.
* **5. Fleet Emergency Stop** → red banner, every tile flips to STOPPED, feed fills with
  `fleet_emergency_stop_active`.
* **Resume Fleet** → everyone comes back **except** the revoked agent — "revocation is a credential kill, not a pause."

## 6. Provable after the fact (45 s)

Audit log → filter **Outcome: Operator actions** — every stop, revoke and policy change is in the chain too.
Click **Verify Chain Integrity** → `VALID (N ENTRIES UNBROKEN)`.

> "Edit, delete or reorder one row and verification pinpoints exactly where the chain broke."

## Backup Q&A

**Why not OPA/Cedar?** The rule order is deliberately simple and pure so it can be compiled to OPA/Cedar later; for
the demo a ~1 µs in-process engine with zero network hops is the right trade.

**How does it scale?** Engine is stateless; state moves to PostgreSQL (row locks per agent) and Redis (rate windows,
pub/sub). Metrics already speak Prometheus.

**Is the chain tamper-proof?** Tamper-*evident*. Production adds WORM storage and periodic external notarisation of
the head hash returned by `/audit-log/verify`.
