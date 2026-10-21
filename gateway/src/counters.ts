/**
 * Live counters through the same Lua scripts the control plane uses (policies/lua), so the data plane
 * and the control plane enforce identical semantics. Keys embed the agent's local date (rollover at
 * local midnight); `{ws:agent}` is a Redis Cluster hash tag.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { Redis } from "ioredis";

export class CounterUnavailable extends Error {}

export interface Limits {
  spend_cap_minor?: number | null;
  action_daily_cap_minor?: number | null;
  rate_limit_per_minute?: number | null;
}

export interface Reservation {
  id: string;
  keys: string[];
  amountMinor: number;
}

export function localDate(tz: string, at = new Date()): string {
  // en-CA formats as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(at);
}

export function counterKeys(ws: string, agent: string, action: string, tz: string, at = new Date()): string[] {
  const tag = `{${ws}:${agent}}`;
  const day = localDate(tz, at);
  return [`vq:${tag}:spend:${day}`, `vq:${tag}:aspend:${action}:${day}`, `vq:${tag}:rate`];
}

type ScriptRedis = Redis & {
  vqReserve(k1: string, k2: string, k3: string, ...args: (string | number)[]): Promise<[number, string, number]>;
  vqRelease(k1: string, k2: string, k3: string, ...args: (string | number)[]): Promise<number>;
  vqAdjust(k1: string, k2: string, ...args: (string | number)[]): Promise<number>;
};

export class Counters {
  readonly redis: ScriptRedis;

  constructor(redis: Redis, luaDir: string, private readonly windowMs: number, private readonly ttlS: number) {
    redis.defineCommand("vqReserve", { numberOfKeys: 3, lua: readFileSync(path.join(luaDir, "reserve.lua"), "utf8") });
    redis.defineCommand("vqRelease", { numberOfKeys: 3, lua: readFileSync(path.join(luaDir, "release.lua"), "utf8") });
    redis.defineCommand("vqAdjust", { numberOfKeys: 2, lua: readFileSync(path.join(luaDir, "adjust.lua"), "utf8") });
    this.redis = redis as ScriptRedis;
  }

  async reserve(ws: string, agent: string, action: string, tz: string, amountMinor: number, limits: Limits):
    Promise<{ ok: true; reservation: Reservation } | { ok: false; rule: string }> {
    const keys = counterKeys(ws, agent, action, tz);
    const id = randomUUID();
    const lim = (v: number | null | undefined) => (v === null || v === undefined ? -1 : v);
    try {
      const [ok, rule] = await this.redis.vqReserve(
        keys[0]!, keys[1]!, keys[2]!,
        amountMinor, lim(limits.spend_cap_minor), lim(limits.action_daily_cap_minor), lim(limits.rate_limit_per_minute),
        Date.now(), this.windowMs, id, this.ttlS,
      );
      if (ok === 1) return { ok: true, reservation: { id, keys, amountMinor } };
      return { ok: false, rule };
    } catch (err) {
      throw new CounterUnavailable((err as Error).message);
    }
  }

  /** Roll back a reservation, e.g. when the upstream call fails. Idempotent. */
  async release(r: Reservation): Promise<void> {
    try {
      await this.redis.vqRelease(r.keys[0]!, r.keys[1]!, r.keys[2]!, r.amountMinor, r.id);
    } catch {
      // Best effort: the reconciliation job corrects drift against the ledger.
    }
  }

  /** Correct an estimate (e.g. LLM tokens) to the actual cost once known. */
  async adjust(r: Reservation, actualMinor: number): Promise<void> {
    const delta = actualMinor - r.amountMinor;
    if (delta === 0) return;
    try {
      await this.redis.vqAdjust(r.keys[0]!, r.keys[1]!, delta);
    } catch {
      // Best effort, as above.
    }
  }
}
