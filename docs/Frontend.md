# Frontend

React 19 + Vite 8 + Tailwind CSS v4 + lucide-react. Single page, dark "control tower" theme.

## Layout

```
frontend/src/
├── App.jsx                     # state, data fetching, operator handlers
├── api.js                      # fetch wrapper (surfaces backend `detail` errors), helpers
├── policyFormat.js             # parse/format action lists and per-action caps
├── styles.js                   # status/outcome colour maps
├── hooks/useLiveFeed.js        # auto-reconnecting WebSocket
└── components/
    ├── Header.jsx              # brand, live indicator, fleet controls, Register Agent
    ├── ScenarioPanel.jsx       # 5 one-click demo scenarios
    ├── MetricsPanel.jsx        # decisions, approval rate, latency p50/p99, blocks by rule
    ├── AgentCard.jsx           # spend meter, limits, allow/deny tabs, stop/resume/revoke
    ├── LiveFeed.jsx            # streaming decisions + operator events (pauses on hover)
    ├── AuditLog.jsx            # filterable ledger + Verify Chain Integrity
    ├── PolicyEditorModal.jsx   # edit allow/deny, caps, per-tx, rate, per-action caps, note
    ├── PolicyHistoryModal.jsx  # version timeline + one-click rollback
    ├── CreateAgentModal.jsx    # register a new agent
    └── ui.jsx                  # Button, Badge, Modal, Field primitives
```

## Real-time model

* `useLiveFeed` keeps one WebSocket to `/ws/live-feed`, reconnecting every 3 s if dropped.
* Every message is a ledger entry; when it carries `_agents`, those agent snapshots replace local state — so spend
  meters, status badges and policy versions update with **no polling**.
* Metrics poll `GET /metrics` every 2 s.
* The audit table prepends live entries that match the active filters.

## Operator workflows

| Goal | UI |
|---|---|
| Change permissions / caps | Agent card → **Policy** → Publish (bumps version, applies to the next action) |
| Undo a bad policy | Agent card → **History** → *Roll back to vN* |
| Pause one agent | Agent card → **Stop** / **Resume** |
| Kill an agent's credentials | Agent card → **Revoke** (asks for a reason) / **Reinstate** |
| Halt everything | Header → **Fleet Emergency Stop** (confirm) → red banner; **Resume Fleet** lifts it |
| Onboard an agent | Header → **Register Agent** |
| Prove integrity | Audit log → **Verify Chain Integrity** |

## Configuration

`VITE_API_BASE_URL` (default `http://localhost:8000`). The WebSocket URL is derived from it.
