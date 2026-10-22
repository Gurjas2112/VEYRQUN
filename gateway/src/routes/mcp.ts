/**
 * POST /mcp — Model Context Protocol (JSON-RPC 2.0 over Streamable HTTP), governed.
 * tools/call -> action = tool name (same names the policies already use); amount from the bundle's
 * per-tool JSON pointer. tools/list is filtered to the tools this agent may call. Tool calls have side
 * effects in the bank core, so they are never retried.
 */
import type { FastifyInstance } from "fastify";
import { z } from "zod";
import type { Deps } from "../app.js";
import { remember } from "../explain.js";
import { sha256 } from "../events.js";
import { decimalToMinor } from "../pipeline.js";
import { StreamScanner } from "../stream-scan.js";
import { callUpstream, UpstreamTimeout } from "../upstream.js";
import { authenticate, sendGoverned } from "./common.js";

const RpcRequest = z.object({
  jsonrpc: z.literal("2.0"),
  id: z.union([z.string().max(128), z.number()]).optional(),
  method: z.string().max(128),
  params: z.record(z.string(), z.unknown()).optional(),
});

const ToolCallParams = z.object({
  name: z.string().regex(/^[a-z][a-z0-9_.-]{1,63}$/),
  arguments: z.record(z.string(), z.unknown()).default({}),
});

function pointer(obj: unknown, ptr: string): unknown {
  return ptr.split("/").slice(1).reduce<unknown>((o, key) =>
    o && typeof o === "object" ? (o as Record<string, unknown>)[key.replace(/~1/g, "/").replace(/~0/g, "~")] : undefined, obj);
}

function forwardHeaders(sessionId: string | undefined): Record<string, string> {
  const h: Record<string, string> = { "content-type": "application/json", accept: "application/json, text/event-stream" };
  if (sessionId) h["mcp-session-id"] = sessionId;
  return h;
}

export function registerMcp(app: FastifyInstance, deps: Deps): void {
  app.post("/mcp", async (req, reply) => {
    const t0 = process.hrtime.bigint();
    const identity = await authenticate(req, reply, deps);
    if (!identity) return reply;
    const parsed = RpcRequest.safeParse(req.body);
    if (!parsed.success) {
      return reply.code(400).send({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "invalid JSON-RPC request" } });
    }
    const rpc = parsed.data;
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    const cfg = deps.cfg;
    const upstreamOpts = { connectTimeoutMs: cfg.UPSTREAM_CONNECT_TIMEOUT_MS, totalTimeoutMs: cfg.UPSTREAM_TOTAL_TIMEOUT_MS };
    const mcpBase = cfg.UPSTREAM_MCP_URL.replace(/\/mcp$/, "");

    if (rpc.method !== "tools/call") {
      // initialize, ping, notifications, tools/list: authenticated pass-through (no spend), idempotent reads.
      let up;
      try {
        up = await callUpstream([mcpBase], "/mcp", { ...upstreamOpts, body: JSON.stringify(rpc), headers: forwardHeaders(sessionId),
          idempotent: rpc.method !== "initialize" });
      } catch (err) {
        const timeout = err instanceof UpstreamTimeout;
        return reply.code(timeout ? 504 : 502).send({ jsonrpc: "2.0", id: rpc.id ?? null,
          error: { code: -32002, message: timeout ? "upstream_timeout" : "upstream_unavailable" } });
      }
      const session = up.res.headers["mcp-session-id"];
      if (session) reply.header("mcp-session-id", String(session));
      if (up.res.statusCode === 202 || up.res.statusCode === 204) {
        await up.res.body.dump();
        return reply.code(up.res.statusCode).send();
      }
      const json = (await up.res.body.json()) as { result?: { tools?: Array<{ name: string }> } };
      if (rpc.method === "tools/list" && json.result?.tools) {
        const bundle = await deps.bundles.get(identity.workspaceId).catch(() => null);
        const agent = bundle?.data.agents[identity.agentId];
        const allowed = new Set(agent && agent.status === "active" ? agent.allowed_actions : []);
        const blocked = new Set(agent?.blocked_actions ?? []);
        json.result.tools = json.result.tools.filter((t) => allowed.has(t.name) && !blocked.has(t.name));
      }
      return reply.code(up.res.statusCode).send(json);
    }

    const call = ToolCallParams.safeParse(rpc.params ?? {});
    if (!call.success) {
      return reply.code(400).send({ jsonrpc: "2.0", id: rpc.id ?? null, error: { code: -32602, message: "invalid tool call params" } });
    }
    const { name, arguments: args } = call.data;
    const bundle = await deps.bundles.get(identity.workspaceId).catch(() => null);
    const currency = bundle?.data.agents[identity.agentId]?.currency ?? "USD";
    const ptr = bundle?.data.settings?.tool_amounts?.[name];
    let amountMinor = 0;
    if (ptr) {
      const parsedAmount = decimalToMinor(pointer(args, ptr), currency);
      if (parsedAmount === null) {
        return reply.code(400).send({ jsonrpc: "2.0", id: rpc.id ?? null,
          error: { code: -32602, message: `argument at ${ptr} must be a non-negative decimal amount` } });
      }
      amountMinor = parsedAmount;
    }
    const argsText = JSON.stringify(args);
    const result = await deps.pipeline.govern({ identity, action: name, actionKind: "tool", amountMinor, text: argsText,
      approvalId: req.headers["x-veyrqun-approval"] as string | undefined });
    remember(req, result.ctx);
    if (result.kind === "respond") return sendGoverned(reply, result, deps, rpc.id ?? null);
    const ctx = result.ctx;
    const tUp = process.hrtime.bigint();
    let up;
    try {
      up = await callUpstream([mcpBase], "/mcp", { ...upstreamOpts, body: JSON.stringify(rpc), headers: forwardHeaders(sessionId),
        idempotent: false });
    } catch (err) {
      // The tool may or may not have run, so the reservation is kept and the reconciliation job settles it.
      const timeout = err instanceof UpstreamTimeout;
      await deps.pipeline.finish(ctx, { upstream_status: timeout ? 504 : 502 });
      return reply.code(timeout ? 504 : 502).header("x-veyrqun-decision-id", ctx.decisionId)
        .send({ jsonrpc: "2.0", id: rpc.id ?? null, error: { code: -32002, message: timeout ? "upstream_timeout" : "upstream_unavailable",
          data: { decision_id: ctx.decisionId } } });
    }
    const json = (await up.res.body.json()) as { result?: { content?: Array<{ type: string; text?: string }>; isError?: boolean } };
    const upstreamMs = Number(process.hrtime.bigint() - tUp) / 1e6;
    ctx.timings.upstream = upstreamMs;
    if (json.result?.isError && ctx.reservation) await deps.counters.release(ctx.reservation); // tool reported no effect
    for (const part of json.result?.content ?? []) {
      if (part.type !== "text" || typeof part.text !== "string") continue;
      const scan = new StreamScanner(cfg.STREAM_SCAN_HOLDBACK, ctx.responseRedact, ctx.responseBlock);
      // push() releases everything except the holdback tail; end() releases the tail. Both parts are the text.
      const head = scan.push(part.text);
      const tail = head.kind === "abort" ? head : scan.end();
      const v = head.kind === "emit" && tail.kind === "emit" ? { ...tail, text: head.text + tail.text } : tail;
      if (v.kind === "abort") {
        deps.metrics.streamAborts.inc({ data_class: v.cls });
        await deps.pipeline.finish(ctx, { upstream_status: up.res.statusCode, stream_aborted: true });
        return reply.code(403).header("x-veyrqun-decision-id", ctx.decisionId).send({ jsonrpc: "2.0", id: rpc.id ?? null,
          error: { code: -32001, message: "response_policy_violation", data: { data_class: v.cls, decision_id: ctx.decisionId } } });
      }
      part.text = v.text;
    }
    const overheadMs = Number(process.hrtime.bigint() - t0) / 1e6 - upstreamMs;
    ctx.timings.overhead = overheadMs;
    deps.metrics.overhead.observe({ route: "mcp" }, overheadMs / 1000);
    deps.metrics.upstream.observe({ route: "mcp", status: String(up.res.statusCode) }, upstreamMs / 1000);
    await deps.pipeline.finish(ctx, { upstream_status: up.res.statusCode, request_sha256: sha256(argsText),
      response_sha256: sha256(JSON.stringify(json)) });
    return reply.code(up.res.statusCode).header("x-veyrqun-decision-id", ctx.decisionId)
      .header("server-timing", `gateway;dur=${overheadMs.toFixed(2)}, upstream;dur=${upstreamMs.toFixed(2)}`).send(json);
  });
}
