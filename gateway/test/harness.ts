/**
 * Integration harness: real Redis, real policy.wasm, real HTTP. Only the control plane is faked (JWKS,
 * signed bundles, approvals), so gateway behaviour can be tested in isolation, including outages.
 */
import { createHash, generateKeyPairSync, sign, type KeyObject } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import Fastify, { type FastifyInstance } from "fastify";
import { Redis } from "ioredis";
import { SignJWT, exportJWK } from "jose";
import { buildApp, buildDeps, shutdown, type Deps } from "../src/app.js";
import { ConfigSchema, type Config } from "../src/config.js";
import { buildMockLlm } from "../src/mock/llm.js";
import { buildMockMcp } from "../src/mock/bankcore-mcp.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
export const WS = "0193a7d0-0000-7000-8000-000000000001";
export const REDIS_URL = process.env.TEST_REDIS_URL ?? "redis://localhost:56379/11";

export function agentDoc(over: Record<string, unknown> = {}) {
  return {
    status: "active", mode: "enforce", policy_version: 3, currency: "USD", timezone: "UTC",
    allowed_actions: ["llm.chat", "flight_rebook", "account_summary"], blocked_actions: ["credit_limit_increase"],
    spend_cap_daily_minor: 150_000, max_per_transaction_minor: 60_000, action_limits_minor: {}, action_daily_caps_minor: {},
    rate_limit_per_minute: 100, approval_threshold_minor: 50_000,
    redact_data_classes: ["email"], block_data_classes: ["pci_pan"],
    ...over,
  };
}

export function defaultData() {
  return {
    agents: {
      travel_agent_01: agentDoc(),
      observer_agent: agentDoc({ mode: "observe", allowed_actions: [] }),
      limited_agent: agentDoc({ rate_limit_per_minute: 2, approval_threshold_minor: null }),
      reader_agent: agentDoc({ allowed_actions: ["account_summary"], approval_threshold_minor: null }),
    },
    fleet: { stopped: false, version: 0 },
    settings: { injection_block_threshold: 0.9, fail_open_safe_actions: ["account_summary"],
      tool_amounts: { flight_rebook: "/fare", wire_transfer: "/amount", credit_limit_increase: "/increase" } },
    revision: 1,
  };
}

function tar(files: Record<string, Buffer>): Buffer {
  const blocks: Buffer[] = [];
  for (const [name, data] of Object.entries(files)) {
    const h = Buffer.alloc(512, 0);
    h.write(name, 0, "utf8");
    h.write("0000644\0", 100); h.write("0000000\0", 108); h.write("0000000\0", 116);
    h.write(data.length.toString(8).padStart(11, "0") + "\0", 124);
    h.write("00000000000\0", 136);
    h.write("        ", 148); h.write("0", 156); h.write("ustar\0", 257); h.write("00", 263);
    let sum = 0;
    for (const b of h) sum += b;
    h.write(sum.toString(8).padStart(6, "0") + "\0 ", 148);
    blocks.push(h, data, Buffer.alloc((512 - (data.length % 512)) % 512, 0));
  }
  blocks.push(Buffer.alloc(1024, 0));
  return Buffer.concat(blocks);
}

export class FakeControlPlane {
  app: FastifyInstance = Fastify({ logger: false });
  private key: KeyObject;
  private publicJwk: Record<string, unknown> = {};
  data = defaultData();
  revision = 1;
  approvals = new Map<string, { status: string; agent_id: string; action_type: string; amount: string; currency: string }>();
  consumed: string[] = [];
  bundleFetches = 0;
  url = "";

  constructor() {
    const { privateKey } = generateKeyPairSync("ed25519");
    this.key = privateKey;
    const wasm = readFileSync(path.join(repo, "policies", "dist", "policy.wasm"));
    this.app.get("/.well-known/jwks.json", async () => ({ keys: [this.publicJwk] }));
    this.app.get("/bundles/current", async (req, reply) => {
      this.bundleFetches++;
      const body = gzipSync(tar({ ".manifest": Buffer.from(JSON.stringify({ revision: String(this.revision) })),
        "data.json": Buffer.from(JSON.stringify({ policy_data: this.data })), "policy.wasm": wasm }));
      const etag = createHash("sha256").update(body).digest("hex");
      reply.header("etag", `"${etag}"`).header("x-veyrqun-bundle-revision", String(this.revision))
        .header("x-veyrqun-signature", sign(null, body, this.key).toString("base64url"))
        .header("x-veyrqun-signature-kid", "test-kid");
      if ((req.headers["if-none-match"] ?? "") === `"${etag}"`) return reply.code(304).send();
      return reply.type("application/gzip").send(body);
    });
    this.app.post("/internal/approvals", async (req, reply) => {
      const b = req.body as { agent_id: string; action_type: string; amount: string; currency: string };
      const id = `appr-${this.approvals.size + 1}`;
      this.approvals.set(id, { status: "pending", ...b });
      return reply.code(201).send({ approval_id: id, expires_at: new Date(Date.now() + 86_400_000).toISOString() });
    });
    this.app.get<{ Params: { id: string } }>("/internal/approvals/:id", async (req, reply) => {
      const a = this.approvals.get(req.params.id);
      if (!a) return reply.code(404).send({});
      return { approval_id: req.params.id, ...a };
    });
    this.app.post<{ Params: { id: string } }>("/internal/approvals/:id/consume", async (req, reply) => {
      const a = this.approvals.get(req.params.id);
      if (!a || a.status !== "approved") return reply.code(409).send({});
      a.status = "consumed";
      this.consumed.push(req.params.id);
      return { status: "consumed" };
    });
  }

  async start(): Promise<void> {
    this.publicJwk = { ...(await exportJWK(this.key)), kid: "test-kid", use: "sig", alg: "EdDSA" };
    delete this.publicJwk.d; // public part only
    this.url = await this.app.listen({ port: 0, host: "127.0.0.1" });
  }

  async token(agent: string, over: { exp?: string; aud?: string; ws?: string } = {}): Promise<string> {
    return new SignJWT({ ws: over.ws ?? WS, scope: "actions:execute" })
      .setProtectedHeader({ alg: "EdDSA", kid: "test-kid" })
      .setSubject(agent).setIssuer("https://veyrqun.local").setAudience(over.aud ?? "veyrqun-gateway")
      .setIssuedAt().setExpirationTime(over.exp ?? "5m").sign(this.key);
  }
}

export interface Harness {
  cp: FakeControlPlane;
  gatewayUrl: string;
  deps: Deps;
  redis: Redis;
  llmUrl: string;
  close(): Promise<void>;
}

export async function startHarness(over: Partial<Record<keyof Config, string>> = {}): Promise<Harness> {
  const cp = new FakeControlPlane();
  await cp.start();
  const llm = buildMockLlm({ name: "primary" });
  const llmUrl = await llm.listen({ port: 0, host: "127.0.0.1" });
  const mcp = buildMockMcp();
  const mcpUrl = await mcp.listen({ port: 0, host: "127.0.0.1" });
  const redis = new Redis(REDIS_URL);
  await redis.flushdb();
  const cfg = ConfigSchema.parse({
    NODE_ENV: "test", LOG_LEVEL: "silent", PORT: "8080", HOST: "127.0.0.1",
    CONTROL_PLANE_URL: cp.url, REDIS_URL, UPSTREAM_LLM_URLS: llmUrl, UPSTREAM_MCP_URL: `${mcpUrl}/mcp`,
    UPSTREAM_TOTAL_TIMEOUT_MS: "1000", FLEET_POLL_MS: "200", BUNDLE_POLL_MS: "200", BUNDLE_MAX_STALENESS_S: "300",
    ...over,
  });
  const deps = buildDeps(cfg);
  const app = await buildApp(deps);
  await deps.control.start();
  const gatewayUrl = await app.listen({ port: 0, host: "127.0.0.1" });
  return {
    cp, gatewayUrl, deps, redis, llmUrl,
    async close() {
      await shutdown(app, deps);
      await Promise.all([cp.app.close(), llm.close(), mcp.close()]);
      redis.disconnect();
    },
  };
}
