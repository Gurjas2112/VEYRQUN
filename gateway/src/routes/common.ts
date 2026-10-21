import type { FastifyReply, FastifyRequest } from "fastify";
import type { Deps } from "../app.js";
import { AuthError, type AgentIdentity } from "../auth.js";
import type { GovernResult } from "../pipeline.js";

/** 401 for a missing/invalid/expired agent JWT. Returns null after replying. */
export async function authenticate(req: FastifyRequest, reply: FastifyReply, deps: Deps): Promise<AgentIdentity | null> {
  try {
    return await deps.auth.verify(req.headers.authorization);
  } catch (err) {
    const reason = err instanceof AuthError ? err.message : "invalid token";
    reply.code(401).header("www-authenticate", 'Bearer realm="veyrqun", error="invalid_token"')
      .send({ error: { type: "unauthorized", message: reason } });
    return null;
  }
}

export async function sendGoverned(reply: FastifyReply, result: Extract<GovernResult, { kind: "respond" }>, deps: Deps,
  jsonRpcId?: string | number | null): Promise<FastifyReply> {
  if (result.ctx) await deps.pipeline.finish(result.ctx, { upstream_status: null });
  for (const [k, v] of Object.entries(result.headers ?? {})) reply.header(k, v);
  const decisionId = (result.body.error as { decision_id?: string } | undefined)?.decision_id
    ?? (result.body.decision_id as string | undefined) ?? result.ctx?.decisionId;
  if (decisionId) reply.header("x-veyrqun-decision-id", decisionId);
  if (jsonRpcId !== undefined && result.status !== 202) {
    // MCP clients expect JSON-RPC errors; the HTTP status still follows the failure contract.
    const err = result.body.error as Record<string, unknown>;
    return reply.code(result.status).send({ jsonrpc: "2.0", id: jsonRpcId, error: { code: -32001, message: String(err.type),
      data: err } });
  }
  return reply.code(result.status).send(result.body);
}
