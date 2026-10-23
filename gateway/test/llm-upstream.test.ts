import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { Redis } from "ioredis";
import { BudgetExhausted, LlmBudget, LlmUnavailable, MetadataToken, upstreamModel } from "../src/llm-upstream.js";
import { REDIS_URL } from "./harness.js";

const okFetch = (token: string, calls: { n: number }) => (async () => {
  calls.n++;
  return new Response(JSON.stringify({ access_token: token, expires_in: 3600 }), { status: 200 });
}) as unknown as typeof fetch;

describe("Workload Identity token from the metadata server", () => {
  it("caches the token until a minute before expiry, then refreshes", async () => {
    let now = 0;
    const calls = { n: 0 };
    const t = new MetadataToken("http://md/token", () => now, okFetch("abc", calls));
    expect(await t.header()).toBe("Bearer abc");
    now = 3_500_000;
    await t.header();
    expect(calls.n).toBe(1);
    now = 3_545_000; // within 60 s of expiry
    await t.header();
    expect(calls.n).toBe(2);
  });

  it("sends the Metadata-Flavor header and coalesces concurrent refreshes", async () => {
    let headers: Headers | undefined;
    const calls = { n: 0 };
    const f = (async (_u: string, init: RequestInit) => {
      calls.n++;
      headers = new Headers(init.headers);
      await new Promise((r) => setTimeout(r, 10));
      return new Response(JSON.stringify({ access_token: "x", expires_in: 3600 }));
    }) as unknown as typeof fetch;
    const t = new MetadataToken("http://md/token", Date.now, f);
    await Promise.all([t.header(), t.header(), t.header()]);
    expect(calls.n).toBe(1);
    expect(headers?.get("metadata-flavor")).toBe("Google");
  });

  it("fails closed with LlmUnavailable when the metadata server is down or returns junk", async () => {
    const down = new MetadataToken("http://md/token", Date.now, (async () => { throw new Error("ECONNREFUSED"); }) as unknown as typeof fetch);
    await expect(down.header()).rejects.toBeInstanceOf(LlmUnavailable);
    const junk = new MetadataToken("http://md/token", Date.now, (async () => new Response("{}")) as unknown as typeof fetch);
    await expect(junk.header()).rejects.toBeInstanceOf(LlmUnavailable);
  });
});

describe("global daily model budget", () => {
  const redis = new Redis(REDIS_URL);
  beforeAll(async () => { await redis.del(`vq:llm-budget:2026-01-01`); });
  afterAll(async () => { await redis.del(`vq:llm-budget:2026-01-01`); redis.disconnect(); });
  const day = () => new Date("2026-01-01T10:00:00Z");

  it("reserves up to the cap, refuses beyond it without consuming, and corrects from usage", async () => {
    const b = new LlmBudget(redis, 100, day);
    await b.reserve(60);
    await expect(b.reserve(50)).rejects.toBeInstanceOf(BudgetExhausted);
    expect(Number(await redis.get("vq:llm-budget:2026-01-01"))).toBe(60);
    await b.adjust(-20); // actual cost was lower than the estimate
    await b.reserve(50);
    expect(Number(await redis.get("vq:llm-budget:2026-01-01"))).toBe(90);
  });

  it("is a no-op when no cap is configured", async () => {
    await new LlmBudget(redis, undefined, day).reserve(10_000_000);
  });
});

describe("model mapping", () => {
  it("rewrites only mapped models", () => {
    const map = { "gemini-3.5-flash": "google/gemini-3.5-flash" };
    expect(upstreamModel(map, "gemini-3.5-flash")).toBe("google/gemini-3.5-flash");
    expect(upstreamModel(map, "mock-model")).toBe("mock-model");
  });
});
