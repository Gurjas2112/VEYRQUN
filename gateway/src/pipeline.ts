/**
 * The governance pipeline every request goes through, in order:
 *   bundle (fail closed if stale) -> classify data -> approval check -> decide (in-process OPA WASM)
 *   -> audit backpressure check -> counters (atomic Lua; fail closed) -> proceed or respond.
 * Status codes follow the failure-mode contract (docs/AGENT_PROMPT.md section 4).
 */
import { injectionSignal } from "./signals.js";
import { randomUUID } from "node:crypto";
import type { AgentIdentity } from "./auth.js";
import { ApprovalsClient } from "./approvals.js";
import { BundleError, BundleStore, type AgentPolicyDoc, type Decision, type LoadedBundle } from "./bundle.js";
import { classify, type Span } from "./classify.js";
import type { Config } from "./config.js";
import type { ControlChannel } from "./control.js";
import { CounterUnavailable, Counters, type Reservation } from "./counters.js";
import { EventSink, type DecisionEvent } from "./events.js";
import type { Metrics } from "./metrics.js";

const CURRENCY_EXPONENT: Record<string, number> = { USD: 2, EUR: 2, GBP: 2, INR: 2, SGD: 2, JPY: 0 };

export const exponentFor = (currency: string): number => CURRENCY_EXPONENT[currency] ?? 2;

export function minorToDecimalString(minor: number, currency: string): string {
  const exp = exponentFor(currency);
  if (exp === 0) return String(minor);
  const s = String(Math.abs(minor)).padStart(exp + 1, "0");
  return `${minor < 0 ? "-" : ""}${s.slice(0, -exp)}.${s.slice(-exp)}`;
}

/** Exact decimal string (major units) -> integer minor units, rejecting extra precision (no floats). */
export function decimalToMinor(value: unknown, currency: string): number | null {
  const exp = exponentFor(currency);
  const s = typeof value === "number" ? String(value) : typeof value === "string" ? value.trim() : "";
  const m = /^(\d{1,15})(?:\.(\d+))?$/.exec(s);
  if (!m) return null;
  const frac = m[2] ?? "";
  if (frac.length > exp) return null;
  return Number(m[1]) * 10 ** exp + Number(frac.padEnd(exp, "0") || "0");
}

export interface GovernInput {
  identity: AgentIdentity;
  action: string;
  actionKind: "llm" | "tool";
  amountMinor: number;
  text: string;
  approvalId?: string;
}

export interface Context {
  decisionId: string;
  identity: AgentIdentity;
  action: string;
  actionKind: "llm" | "tool";
  amountMinor: number;
  currency: string;
  bundle: LoadedBundle;
  agent: AgentPolicyDoc | undefined;
  decision: Decision;
  enforced: boolean;
  effectiveOutcome: string;
  classes: string[];
  spans: Span[];
  signals: { injection_score: number | null; injection_patterns: string[] };
  requestRedactions: Set<string>;
  responseRedact: Set<string>;
  responseBlock: Set<string>;
  reservation?: Reservation;
  approvalId?: string;
  timings: Record<string, number>;
  startedAt: number;
}

export type GovernResult =
  | { kind: "proceed"; ctx: Context }
  | { kind: "respond"; status: number; body: Record<string, unknown>; headers?: Record<string, string>; ctx?: Context };

const ms = (t0: bigint) => Number(process.hrtime.bigint() - t0) / 1e6;

export class Pipeline {
  private backlogCache = { at: 0, value: 0 };

  constructor(
    private readonly cfg: Config,
    private readonly bundles: BundleStore,
    private readonly counters: Counters,
    private readonly control: ControlChannel,
    private readonly events: EventSink,
    private readonly approvals: ApprovalsClient,
    private readonly metrics: Metrics,
  ) {}

  private deny(status: number, type: string, ctx: Context | undefined, extra: Record<string, unknown> = {},
    headers?: Record<string, string>): GovernResult {
    return {
      kind: "respond", status, headers, ctx,
      body: { error: { type, rule_id: ctx?.decision.rule_id ?? null, decision_id: ctx?.decisionId, ...extra } },
    };
  }

  private async auditBacklog(): Promise<number> {
    if (Date.now() - this.backlogCache.at > 1_000) {
      this.backlogCache = { at: Date.now(), value: await this.events.backlog() };
      this.metrics.auditBacklog.set(this.backlogCache.value);
    }
    return this.backlogCache.value;
  }

  async govern(input: GovernInput): Promise<GovernResult> {
    const startedAt = Date.now();
    const timings: Record<string, number> = {};
    const decisionId = randomUUID();
    const ws = input.identity.workspaceId;

    let t = process.hrtime.bigint();
    let bundle: LoadedBundle;
    try {
      bundle = await this.bundles.get(ws);
    } catch (err) {
      const reason = err instanceof BundleError ? err.message : "policy unavailable";
      return { kind: "respond", status: 503, headers: { "retry-after": "5" },
        body: { error: { type: "policy_unavailable", message: reason, decision_id: decisionId } } };
    }
    timings.bundle = ms(t);

    const agent = bundle.data.agents[input.identity.agentId];
    const currency = agent?.currency ?? "USD";

    t = process.hrtime.bigint();
    const { classes, spans } = classify(input.text);
    const injection = injectionSignal(input.text);
    timings.classify = ms(t);

    let approvalGranted = false;
    if (input.approvalId) {
      const a = await this.approvals.status(ws, input.approvalId).catch(() => null);
      const amount = minorToDecimalString(input.amountMinor, currency);
      if (!a || a.agent_id !== input.identity.agentId) {
        return { kind: "respond", status: 404, body: { error: { type: "approval_not_found", decision_id: decisionId } } };
      }
      if (a.status !== "approved" || a.action_type !== input.action || a.amount !== amount) {
        return { kind: "respond", status: 409,
          body: { error: { type: "approval_not_usable", status: a.status, decision_id: decisionId } } };
      }
      approvalGranted = true;
    }

    t = process.hrtime.bigint();
    const decision = this.bundles.evaluate(bundle, {
      agent_id: input.identity.agentId,
      action: input.action,
      amount_minor: input.amountMinor,
      data_classes: classes,
      signals: { injection_score: injection.score },
      approval_granted: approvalGranted,
      fleet_state: { stopped: this.control.isStopped(ws) },
    });
    timings.decide = ms(t);

    const enforced = decision.mode === "enforce";
    const ctx: Context = {
      decisionId, identity: input.identity, action: input.action, actionKind: input.actionKind,
      amountMinor: input.amountMinor, currency, bundle, agent, decision, enforced, effectiveOutcome: decision.outcome,
      classes, spans, signals: { injection_score: injection.score, injection_patterns: injection.matched }, requestRedactions: new Set(decision.redactions ?? []),
      responseRedact: new Set(agent?.redact_data_classes ?? []), responseBlock: new Set(agent?.block_data_classes ?? []),
      approvalId: approvalGranted ? input.approvalId : undefined, timings, startedAt,
    };

    if (this.cfg.AUDIT_MODE === "strict" && (await this.auditBacklog()) > this.cfg.AUDIT_BACKLOG_THRESHOLD) {
      ctx.effectiveOutcome = "block";
      return this.deny(503, "audit_backlog", ctx, { message: "audit pipeline is behind; strict mode refuses new actions" },
        { "retry-after": "10" });
    }

    if (decision.outcome === "block") {
      if (enforced) return this.deny(403, "policy_denied", ctx);
      ctx.effectiveOutcome = "allow";
      return { kind: "proceed", ctx };
    }

    if (decision.outcome === "require_approval" && enforced) {
      const amount = minorToDecimalString(input.amountMinor, currency);
      try {
        const a = await this.approvals.create(ws, input.identity.agentId, input.action, amount, currency, decisionId);
        return { kind: "respond", status: 202, ctx,
          body: { status: "pending_approval", approval_id: a.approval_id, expires_at: a.expires_at, decision_id: decisionId,
            poll: `/v1/approvals/${a.approval_id}`, resume: "retry with header x-veyrqun-approval: <approval_id>" } };
      } catch {
        return this.deny(503, "approvals_unavailable", ctx, {}, { "retry-after": "5" });
      }
    }

    t = process.hrtime.bigint();
    try {
      const tz = agent?.timezone ?? "UTC";
      const r = await this.counters.reserve(ws, input.identity.agentId, input.action, tz, input.amountMinor, decision.limits ?? {});
      if (r.ok) {
        ctx.reservation = r.reservation;
      } else if (enforced) {
        ctx.decision = { ...decision, outcome: "block", rule_id: r.rule };
        ctx.effectiveOutcome = "block";
        timings.counters = ms(t);
        if (r.rule === "rate_limit_exceeded") {
          return this.deny(429, "rate_limited", ctx, {}, { "retry-after": String(Math.ceil(this.cfg.RATE_WINDOW_MS / 1000)) });
        }
        return this.deny(429, "spend_limit_exceeded", ctx);
      }
    } catch (err) {
      if (!(err instanceof CounterUnavailable)) throw err;
      this.metrics.redisErrors.inc();
      if (!decision.fail_open_safe) {
        ctx.decision = { ...decision, outcome: "block", rule_id: "state_store_unavailable" };
        ctx.effectiveOutcome = "block";
        return this.deny(503, "state_store_unavailable", ctx, {}, { "retry-after": "5" });
      }
      ctx.timings.fail_open_safe = 1; // read-only, zero-amount, explicitly marked safe by policy
    }
    timings.counters = ms(t);
    return { kind: "proceed", ctx };
  }

  /** Emit the decision event. Called exactly once per governed request, after the outcome is known. */
  async finish(ctx: Context, extra: Partial<DecisionEvent> = {}): Promise<void> {
    if (ctx.approvalId && extra.upstream_status && extra.upstream_status < 400) {
      await this.approvals.consume(ctx.identity.workspaceId, ctx.approvalId, ctx.decisionId).catch(() => false);
    }
    const kind = ctx.actionKind;
    this.metrics.decisions.inc({ outcome: ctx.decision.outcome, rule: ctx.decision.rule_id ?? "none", action_kind: kind,
      enforced: String(ctx.enforced) });
    const event: DecisionEvent = {
      decision_id: ctx.decisionId,
      ts: new Date(ctx.startedAt).toISOString(),
      workspace_id: ctx.identity.workspaceId,
      actor: `agent:${ctx.identity.agentId}`,
      agent_id: ctx.identity.agentId,
      action: ctx.action,
      amount_minor: ctx.amountMinor,
      currency: ctx.currency,
      outcome: ctx.decision.outcome,
      effective_outcome: ctx.effectiveOutcome,
      rule_id: ctx.decision.rule_id,
      mode: ctx.decision.mode,
      policy_version: ctx.decision.policy_version,
      bundle_revision: ctx.bundle.revision,
      data_classes: ctx.classes,
      signals: ctx.signals,
      redactions: [...ctx.requestRedactions],
      approval_id: ctx.approvalId ?? null,
      timings_ms: ctx.timings,
      ...extra,
    };
    try {
      await this.events.emit(event);
    } catch {
      this.metrics.redisErrors.inc(); // async mode: the request already completed; the alert fires on the metric
    }
  }
}
