/**
 * Credentials and limits for the model upstream.
 *
 * - `UPSTREAM_LLM_AUTH=gcp`: a Google access token from the GKE metadata server (Workload Identity), so the gateway
 *   holds no key at all. Cached until a minute before expiry; one in-flight refresh at a time.
 * - `LLM_DAILY_BUDGET_MINOR`: a hard, deployment-wide cap on model spend per UTC day, on top of per-agent caps.
 *   Reserved up front with the same pre-call estimate the policy uses, corrected from `usage`. Fails closed.
 */
import type { Redis } from "ioredis";

export class LlmUnavailable extends Error {}

export interface TokenSource {
  header(): Promise<string | undefined>;
}

export class StaticToken implements TokenSource {
  constructor(private readonly key: string | undefined) {}
  async header(): Promise<string | undefined> {
    return this.key ? `Bearer ${this.key}` : undefined;
  }
}

export class MetadataToken implements TokenSource {
  private token?: { value: string; expiresAt: number };
  private inflight?: Promise<string>;

  constructor(private readonly url: string, private readonly now: () => number = Date.now,
    private readonly fetchImpl: typeof fetch = fetch) {}

  async header(): Promise<string> {
    if (this.token && this.now() < this.token.expiresAt - 60_000) return `Bearer ${this.token.value}`;
    this.inflight ??= this.refresh().finally(() => { this.inflight = undefined; });
    return `Bearer ${await this.inflight}`;
  }

  private async refresh(): Promise<string> {
    let res: Response;
    try {
      res = await this.fetchImpl(this.url, { headers: { "Metadata-Flavor": "Google" }, signal: AbortSignal.timeout(3_000) });
    } catch (err) {
      throw new LlmUnavailable(`metadata server unreachable: ${(err as Error).message}`);
    }
    if (!res.ok) throw new LlmUnavailable(`metadata server returned ${res.status}`);
    const body = (await res.json()) as { access_token?: string; expires_in?: number };
    if (!body.access_token || !body.expires_in) throw new LlmUnavailable("metadata server returned no token");
    this.token = { value: body.access_token, expiresAt: this.now() + body.expires_in * 1000 };
    return body.access_token;
  }
}

export class BudgetExhausted extends Error {}

/** Global daily model budget in minor units. `limit` undefined = disabled. */
export class LlmBudget {
  constructor(private readonly redis: Redis, private readonly limit: number | undefined,
    private readonly now: () => Date = () => new Date()) {}

  private key(): string {
    return `vq:llm-budget:${this.now().toISOString().slice(0, 10)}`;
  }

  get enabled(): boolean {
    return this.limit !== undefined;
  }

  /** Reserve `amount`; throws BudgetExhausted if it would exceed the cap (and undoes the reservation). */
  async reserve(amount: number): Promise<void> {
    if (this.limit === undefined || amount <= 0) return;
    const key = this.key();
    let total: number;
    try {
      total = await this.redis.incrby(key, amount);
      if (total === amount) await this.redis.expire(key, 2 * 86_400);
    } catch (err) {
      throw new LlmUnavailable(`budget store unavailable: ${(err as Error).message}`); // fail closed
    }
    if (total > this.limit) {
      await this.redis.decrby(key, amount).catch(() => undefined);
      throw new BudgetExhausted(`daily model budget of ${this.limit} minor units is used up`);
    }
  }

  async adjust(delta: number): Promise<void> {
    if (this.limit === undefined || delta === 0) return;
    await this.redis.incrby(this.key(), delta).catch(() => undefined);
  }
}

/** Rewrites the client-facing model name to the provider's id (e.g. gemini-3.5-flash -> google/gemini-3.5-flash). */
export const upstreamModel = (map: Record<string, string>, model: string): string => map[model] ?? model;
