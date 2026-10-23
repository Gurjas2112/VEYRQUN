# Testing the live demo

**URL:** https://136.82.10.126.nip.io. It's open until 2026-10-28 10:40 UTC; after that the link returns "demo ended".

Click **Try the live demo**. You get a private sandbox with four agents and synthetic money. Nobody else sees your
data, and no account or personal data is needed. Then use **Playground**: every request goes through the real
gateway, so the answer is exactly what that agent would get in production.

## Ten things to try (each has a preset button)

| Try | Expect | Why |
|---|---|---|
| `fee_reversal_bot` · `waive_fee` · 25.00 | Allowed, and spend goes up | on the allowlist, under every limit |
| same, 75.00 | Needs approval, then **Approve & re-run** → Allowed | over the $50 approval threshold; the approval only fits this exact action and amount |
| `card_replacement_bot` · `card_issue` · 45.00 | Blocked `transaction_limit_exceeded` | per-transaction limit is $40 |
| `rogue_bot` · `wire_transfer` · 5000 | Blocked `action_not_permitted` | explicitly forbidden for this agent |
| any agent · any made-up tool name | Blocked `action_not_in_allowlist` | default deny |
| text with `4111 1111 1111 1111` | Blocked `data_class_not_permitted` | card numbers may not leave; try dots, full-width digits or line breaks too |
| text with `jane.doe@example.com` | Allowed · redacted | the email is masked before the tool sees it |
| prompt "Ignore all previous instructions…" | Blocked `injection_suspected` | injection signal over the policy threshold (zero-width and look-alike tricks are caught) |
| Model prompt to `travel_bot` (any question) | A real answer from Gemini 3.5 Flash | governed like everything else; spend metered from token usage |
| "Model leaks a card" preset | Stopped mid-answer | the gateway scans what the model *returns*, not just what it's asked |

Then:
- **Audit ledger → Verify whole ledger.** Every decision above is hash-chained, HMAC-signed and anchored. Open
  any entry to see its hash, signature and recorded details.
- **System → Stop all agents** (type STOP). Re-run anything: it is refused within about a second. Then release.
- **Ctrl+K** opens the command palette (jump to an agent, stop an agent, verify the ledger).

## For API testers
**Agents → (any agent) → Connect this agent** shows the token request and an MCP call. Rotate the secret to get
credentials. Send OpenAI-compatible requests to `/v1/chat/completions` (model `gemini-3.5-flash`) or JSON-RPC to
`/mcp`.

**Limits on the demo:** 10 guest sandboxes per IP per hour, and a $5/day cap on model spend across all visitors.
When the cap is reached, model calls return 429 until midnight UTC, and tool calls keep working.
