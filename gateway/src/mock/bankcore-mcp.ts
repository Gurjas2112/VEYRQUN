/**
 * Mock "bank core" MCP server (JSON-RPC 2.0 over Streamable HTTP). Tools match VEYRQUN action types.
 * Synthetic data only. `{"slow": true}` in arguments delays 3 s; a `leak` argument echoes a card number.
 */
import { randomUUID } from "node:crypto";
import Fastify, { type FastifyInstance } from "fastify";

const TOOLS: Record<string, { description: string; amountArg?: string }> = {
  waive_fee: { description: "Waive a fee on an account", amountArg: "amount" },
  card_issue: { description: "Issue a replacement card", amountArg: "fee" },
  card_lock: { description: "Lock a card" },
  flight_rebook: { description: "Rebook a flight on a travel booking", amountArg: "fare" },
  hotel_rebook: { description: "Rebook a hotel", amountArg: "amount" },
  credit_limit_increase: { description: "Increase a credit limit", amountArg: "increase" },
  wire_transfer: { description: "Send a wire transfer", amountArg: "amount" },
  account_summary: { description: "Read an account summary" },
  balance_check: { description: "Read a balance" },
  score_check: { description: "Read a credit score" },
};

export const calls: Array<{ name: string; arguments: Record<string, unknown> }> = [];

export function buildMockMcp(): FastifyInstance {
  const app = Fastify({ logger: false });
  app.post("/mcp", async (req, reply) => {
    const rpc = req.body as { jsonrpc: string; id?: string | number; method: string; params?: Record<string, unknown> };
    if (rpc.id === undefined) return reply.code(202).send(); // notification
    switch (rpc.method) {
      case "initialize":
        reply.header("mcp-session-id", randomUUID());
        return { jsonrpc: "2.0", id: rpc.id, result: { protocolVersion: "2025-06-18", capabilities: { tools: {} },
          serverInfo: { name: "mock-bank-core", version: "1.0.0" } } };
      case "ping":
        return { jsonrpc: "2.0", id: rpc.id, result: {} };
      case "tools/list":
        return { jsonrpc: "2.0", id: rpc.id, result: { tools: Object.entries(TOOLS).map(([name, t]) => ({
          name, description: t.description,
          inputSchema: { type: "object", properties: t.amountArg ? { [t.amountArg]: { type: "string" } } : {} },
        })) } };
      case "tools/call": {
        const name = String(rpc.params?.name);
        const args = (rpc.params?.arguments ?? {}) as Record<string, unknown>;
        if (!TOOLS[name]) return { jsonrpc: "2.0", id: rpc.id, error: { code: -32602, message: `unknown tool ${name}` } };
        if (args.slow) await new Promise((r) => setTimeout(r, 3_000));
        calls.push({ name, arguments: args });
        const text = args.leak ? `Done. Card 4111 1111 1111 1111 updated.` : `Done: ${name} ${JSON.stringify(args)} (synthetic)`;
        return { jsonrpc: "2.0", id: rpc.id, result: { content: [{ type: "text", text }], isError: false } };
      }
      default:
        return { jsonrpc: "2.0", id: rpc.id, error: { code: -32601, message: "method not found" } };
    }
  });
  return app;
}

if (process.argv[1]?.endsWith("bankcore-mcp.ts")) {
  const port = Number(process.env.PORT ?? 9102);
  await buildMockMcp().listen({ port, host: "0.0.0.0" });
  console.log(`mock bank-core MCP on :${port}`);
}
