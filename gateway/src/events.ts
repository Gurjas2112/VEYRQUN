/**
 * Decision events go to a Redis Stream (non-blocking XADD); the audit writer (Phase 3) is the single
 * consumer that hash-chains them into Postgres. Payloads are hashed, not stored, unless AUDIT_PAYLOADS=true.
 */
import { createHash } from "node:crypto";
import type { Redis } from "ioredis";

export interface DecisionEvent {
  decision_id: string;
  ts: string;
  workspace_id: string;
  actor: string;
  agent_id: string;
  action: string;
  amount_minor: number;
  currency: string;
  outcome: string;
  effective_outcome: string;
  rule_id: string | null;
  mode: string;
  policy_version: number;
  bundle_revision: number;
  data_classes: string[];
  signals?: { injection_score: number | null; injection_patterns: string[] };
  redactions: string[];
  approval_id?: string | null;
  upstream_status?: number | null;
  stream_aborted?: boolean;
  request_sha256?: string;
  response_sha256?: string;
  payload?: string;
  timings_ms: Record<string, number>;
}

export const sha256 = (s: string | Buffer): string => createHash("sha256").update(s).digest("hex");

export class EventSink {
  constructor(
    private readonly redis: Redis,
    private readonly stream: string,
    private readonly group: string,
  ) {}

  async emit(e: DecisionEvent): Promise<void> {
    // Approximate trimming keeps memory bounded if the writer is down; the backlog check alerts first.
    await this.redis.xadd(this.stream, "MAXLEN", "~", "1000000", "*", "event", JSON.stringify(e));
  }

  /** Entries not yet delivered to the audit writer's consumer group (or the whole stream before it exists). */
  async backlog(): Promise<number> {
    try {
      const groups = (await this.redis.xinfo("GROUPS", this.stream)) as unknown[][];
      for (const g of groups) {
        const fields = new Map<string, unknown>();
        for (let i = 0; i < g.length; i += 2) fields.set(String(g[i]), g[i + 1]);
        if (fields.get("name") === this.group) {
          const lag = fields.get("lag");
          return typeof lag === "number" ? lag : Number(fields.get("pending") ?? 0);
        }
      }
    } catch {
      // stream doesn't exist yet
    }
    return this.redis.xlen(this.stream).catch(() => 0);
  }
}
