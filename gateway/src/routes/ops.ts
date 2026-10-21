import type { FastifyInstance } from "fastify";
import type { Deps } from "../app.js";
import { authenticate } from "./common.js";

export function registerOps(app: FastifyInstance, deps: Deps): void {
  /** Liveness: the process is up and serving. */
  app.get("/healthz", async () => ({ status: "ok" }));

  /** Readiness: Redis reachable and every loaded policy bundle within its staleness budget. */
  app.get("/readyz", async (_req, reply) => {
    const checks: Record<string, string> = {};
    try {
      await deps.redis.ping();
      checks.redis = "ok";
    } catch {
      checks.redis = "unreachable";
    }
    for (const ws of deps.bundles.workspaces()) {
      const age = deps.bundles.ageSeconds(ws) ?? Infinity;
      deps.metrics.bundleAge.set({ workspace: ws }, age);
      checks[`bundle:${ws}`] = age <= deps.cfg.BUNDLE_MAX_STALENESS_S ? "ok" : `stale (${Math.round(age)}s)`;
    }
    const ready = Object.values(checks).every((v) => v === "ok");
    return reply.code(ready ? 200 : 503).send({ ready, checks });
  });

  app.get("/metrics", async (_req, reply) => {
    for (const ws of deps.bundles.workspaces()) {
      deps.metrics.bundleAge.set({ workspace: ws }, deps.bundles.ageSeconds(ws) ?? -1);
      deps.metrics.killSwitch.set({ workspace: ws }, deps.control.isStopped(ws) ? 1 : 0);
    }
    return reply.type(deps.metrics.registry.contentType).send(await deps.metrics.registry.metrics());
  });

  /** Agents poll their own pending approvals here (the control plane holds the queue). */
  app.get<{ Params: { id: string } }>("/v1/approvals/:id", async (req, reply) => {
    const identity = await authenticate(req, reply, deps);
    if (!identity) return reply;
    const status = await deps.approvals.status(identity.workspaceId, req.params.id).catch(() => null);
    if (!status || status.agent_id !== identity.agentId) return reply.code(404).send({ error: { type: "approval_not_found" } });
    return status;
  });
}
