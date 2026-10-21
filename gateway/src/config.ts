/** All configuration comes from env vars, validated once at startup (fail fast). */
import { fileURLToPath } from "node:url";
import path from "node:path";
import { z } from "zod";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");

const bool = z
  .union([z.boolean(), z.string()])
  .transform((v) => (typeof v === "boolean" ? v : ["1", "true", "yes"].includes(v.toLowerCase())));

const ModelPrice = z.object({ input_per_mtok: z.number().nonnegative(), output_per_mtok: z.number().nonnegative() });

export const ConfigSchema = z.object({
  PORT: z.coerce.number().int().min(1).max(65535).default(8080),
  HOST: z.string().default("0.0.0.0"),
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  LOG_LEVEL: z.enum(["fatal", "error", "warn", "info", "debug", "trace", "silent"]).default("info"),

  CONTROL_PLANE_URL: z.url().default("http://localhost:8000"),
  JWKS_URL: z.url().optional(),
  GATEWAY_SERVICE_TOKEN: z.string().min(1).default("dev-gateway-token"),
  JWT_ISSUER: z.string().default("https://veyrqun.local"),
  JWT_AUDIENCE: z.string().default("veyrqun-gateway"),

  REDIS_URL: z.string().default("redis://localhost:56379/0"),
  LUA_DIR: z.string().default(path.join(repoRoot, "policies", "lua")),

  UPSTREAM_LLM_URLS: z
    .string()
    .default("http://localhost:9101")
    .transform((s) => s.split(",").map((u) => u.trim()).filter(Boolean)),
  UPSTREAM_LLM_API_KEY: z.string().optional(),
  UPSTREAM_MCP_URL: z.url().default("http://localhost:9102/mcp"),
  UPSTREAM_CONNECT_TIMEOUT_MS: z.coerce.number().int().positive().default(2_000),
  UPSTREAM_TOTAL_TIMEOUT_MS: z.coerce.number().int().positive().default(30_000),

  BUNDLE_POLL_MS: z.coerce.number().int().positive().default(2_000),
  BUNDLE_MAX_STALENESS_S: z.coerce.number().int().positive().default(300),
  FLEET_POLL_MS: z.coerce.number().int().positive().default(1_000),

  AUDIT_MODE: z.enum(["async", "strict"]).default("async"),
  AUDIT_STREAM: z.string().default("vq:decisions"),
  AUDIT_GROUP: z.string().default("audit-writer"),
  AUDIT_BACKLOG_THRESHOLD: z.coerce.number().int().positive().default(10_000),
  AUDIT_PAYLOADS: bool.default(false),

  RATE_WINDOW_MS: z.coerce.number().int().positive().default(60_000),
  COUNTER_TTL_S: z.coerce.number().int().positive().default(2 * 86_400),
  STREAM_SCAN_HOLDBACK: z.coerce.number().int().min(32).max(4096).default(64),
  MAX_BODY_BYTES: z.coerce.number().int().positive().default(1_048_576),

  MODEL_PRICES: z
    .string()
    .default(
      JSON.stringify({
        "mock-model": { input_per_mtok: 0.5, output_per_mtok: 1.5 },
        "gpt-4o-mini": { input_per_mtok: 0.15, output_per_mtok: 0.6 },
        "gemini-2.5-flash": { input_per_mtok: 0.3, output_per_mtok: 2.5 },
      }),
    )
    .transform((s, ctx) => {
      try {
        return z.record(z.string(), ModelPrice).parse(JSON.parse(s));
      } catch {
        ctx.addIssue({ code: "custom", message: "MODEL_PRICES must be JSON {model: {input_per_mtok, output_per_mtok}}" });
        return z.NEVER;
      }
    }),
  DEFAULT_MAX_TOKENS: z.coerce.number().int().positive().default(512),
});

export type Config = z.infer<typeof ConfigSchema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const parsed = ConfigSchema.safeParse(env);
  if (!parsed.success) {
    // Fail fast with a readable list of every invalid variable.
    throw new Error(`invalid gateway configuration:\n${z.prettifyError(parsed.error)}`);
  }
  const cfg = parsed.data;
  if (cfg.NODE_ENV === "production" && cfg.GATEWAY_SERVICE_TOKEN === "dev-gateway-token") {
    throw new Error("GATEWAY_SERVICE_TOKEN must be set in production");
  }
  return cfg;
}

export function jwksUrl(cfg: Config): string {
  return cfg.JWKS_URL ?? `${cfg.CONTROL_PLANE_URL.replace(/\/$/, "")}/.well-known/jwks.json`;
}
