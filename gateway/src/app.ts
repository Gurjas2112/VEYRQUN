import { randomUUID } from "node:crypto";
import Fastify, { type FastifyInstance } from "fastify";
import { Redis } from "ioredis";
import { AgentAuth } from "./auth.js";
import { ApprovalsClient } from "./approvals.js";
import { BundleStore } from "./bundle.js";
import { jwksUrl, type Config } from "./config.js";
import { ControlChannel } from "./control.js";
import { Counters } from "./counters.js";
import { EventSink } from "./events.js";
import { Metrics } from "./metrics.js";
import { Pipeline } from "./pipeline.js";
import { registerChat } from "./routes/chat.js";
import { registerMcp } from "./routes/mcp.js";
import { registerOps } from "./routes/ops.js";

export interface Deps {
  cfg: Config;
  redis: Redis;
  subscriber: Redis;
  auth: AgentAuth;
  bundles: BundleStore;
  counters: Counters;
  control: ControlChannel;
  events: EventSink;
  approvals: ApprovalsClient;
  metrics: Metrics;
  pipeline: Pipeline;
}

export function buildDeps(cfg: Config): Deps {
  const redisOpts = { maxRetriesPerRequest: 1, enableOfflineQueue: false, connectTimeout: 1_000, commandTimeout: 500 };
  const redis = new Redis(cfg.REDIS_URL, redisOpts);
  const subscriber = new Redis(cfg.REDIS_URL, { ...redisOpts, commandTimeout: undefined, enableOfflineQueue: true });
  redis.on("error", () => undefined); // surfaced via CounterUnavailable + metrics, not crashes
  subscriber.on("error", () => undefined);
  const metrics = new Metrics();
  const bundles = new BundleStore({ controlPlaneUrl: cfg.CONTROL_PLANE_URL, jwksUrl: jwksUrl(cfg),
    serviceToken: cfg.GATEWAY_SERVICE_TOKEN, maxStalenessS: cfg.BUNDLE_MAX_STALENESS_S });
  const counters = new Counters(redis, cfg.LUA_DIR, cfg.RATE_WINDOW_MS, cfg.COUNTER_TTL_S);
  const control = new ControlChannel(redis, subscriber, bundles, { fleetPollMs: cfg.FLEET_POLL_MS, bundlePollMs: cfg.BUNDLE_POLL_MS });
  const events = new EventSink(redis, cfg.AUDIT_STREAM, cfg.AUDIT_GROUP);
  const approvals = new ApprovalsClient(cfg.CONTROL_PLANE_URL, cfg.GATEWAY_SERVICE_TOKEN);
  const auth = new AgentAuth(jwksUrl(cfg), cfg.JWT_ISSUER, cfg.JWT_AUDIENCE);
  const pipeline = new Pipeline(cfg, bundles, counters, control, events, approvals, metrics);
  return { cfg, redis, subscriber, auth, bundles, counters, control, events, approvals, metrics, pipeline };
}

export async function buildApp(deps: Deps): Promise<FastifyInstance> {
  const app = Fastify({
    logger: {
      level: deps.cfg.LOG_LEVEL,
      redact: ["req.headers.authorization", "req.headers.cookie", "req.headers[\"x-veyrqun-approval\"]"],
    },
    bodyLimit: deps.cfg.MAX_BODY_BYTES,
    genReqId: () => randomUUID(),
    requestTimeout: 0, // streams can be long; per-upstream deadlines apply instead
  });
  // Every response carries a decision id (non-governed routes get the request id).
  app.addHook("onSend", async (req, reply, payload) => {
    if (!reply.hasHeader("x-veyrqun-decision-id")) reply.header("x-veyrqun-decision-id", req.id);
    return payload;
  });
  registerOps(app, deps);
  registerChat(app, deps);
  registerMcp(app, deps);
  return app;
}

export async function shutdown(app: FastifyInstance, deps: Deps): Promise<void> {
  deps.control.stop();
  await app.close();
  deps.redis.disconnect();
  deps.subscriber.disconnect();
}
