# 0013. Agent fleet: governance outside the agent, approvals as durable interrupts

**Status:** accepted · 2026-10-22

## Context
A governance layer is only credible if it holds when the agent misbehaves: a model that follows injected text, agent
code that offers tools it was never granted, output that leaks a card number. And approvals can take minutes or
hours, longer than any agent process should be trusted to stay alive.

## Decisions

**Agents carry no policy.** The fleet (`agents/fleet`) is a single LangGraph `StateGraph` (model → tools → model).
Its client maps gateway answers to routes and nothing else. The rogue agent is built the same way, with misbehaving
*code*. The scenarios check that containment comes from the gateway: an unlisted tool is refused by Rego
(`action_not_permitted`) even though the model was offered it, and a card number the model writes into tool
arguments is stopped by the gateway's output scan.

**Tool calls in model output are governed data.** Building the fleet exposed gaps, now fixed in the gateway:
- Tool-call arguments were not scanned in requests or plain responses.
- Streamed `tool_calls` deltas were dropped entirely.
- An assistant message without `content` was rejected.

Arguments are now classified and redacted in requests, scanned in plain responses, and **buffered until complete,
then scanned, then released** in streams. Partial JSON can't be judged, so nothing is forwarded early.

**Injection is a signal, not a decision.** The gateway computes `injection_score` with a transparent phrase
heuristic over user text, tool results and tool arguments. The existing Rego rule `injection_suspected` compares it
with the bundle threshold. The heuristic is easy to evade (see limits), so it complements deterministic controls
(allowlists, limits, output scanning) and does not replace them.

**Approvals are durable interrupts.**
- On `202 pending_approval` the tool call is parked in graph state and the graph calls `interrupt()`.
- The Postgres checkpointer persists the thread, and any process can resume it.
- After resuming, the graph asks the control plane (through the gateway) for the approval's status. Resuming with
  `{"approved": true}` grants nothing.
- The retry carries `x-veyrqun-approval`. The gateway checks that the approval matches agent, action and amount, and
  consumes it once.
- LangGraph re-runs an interrupted node from the top, so the interrupting node has no side effects and each tool call
  is its own node execution. A resume never repeats a tool call.

**Scenarios are specified before they run.** Each scenario lists the gateway answers it expects. The runner compares
them with the recorded trace, checks that every decision id is in the HMAC-signed ledger, and writes
`docs/results/scenarios.json`. Last run: 12/12 passed, 35/35 governed calls in a valid ledger, 2 approval runs
resumed from the checkpoint in a new graph instance.

## Limits
- The model is a deterministic mock, so scenarios prove the *governance path*, not model quality. Phase 8 adds Gemini
  for the RAG work, with a budget cap.
- The phrase heuristic catches known override patterns and misses paraphrases and other languages. The Phase 9
  classifier plugs in behind the same `injection_score` field. When the classifier is unavailable, the score is
  null, which never blocks.
- In the scenario runner, approvals are decided through the control-plane service layer. In production they come from an
  approver in the dashboard (role `approver`, maker-checker enforced server-side).
