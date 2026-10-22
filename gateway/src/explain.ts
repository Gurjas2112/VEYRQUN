/**
 * Decision explanation for the playground. Only tokens that carry the `veyrqun:explain` scope get it. The control
 * plane mints that scope for its sandbox playground only, so production agents can't use the header to probe
 * thresholds or the injection heuristic.
 */
import type { FastifyRequest } from "fastify";
import type { Context } from "./pipeline.js";

export const EXPLAIN_SCOPE = "veyrqun:explain";
const KEY = Symbol("veyrqun.ctx");

export function remember(req: FastifyRequest, ctx: Context | undefined): void {
  if (ctx) (req as unknown as Record<symbol, Context>)[KEY] = ctx;
}

export function explainFor(req: FastifyRequest): string | null {
  const ctx = (req as unknown as Record<symbol, Context | undefined>)[KEY];
  if (!ctx || !ctx.identity.scopes.includes(EXPLAIN_SCOPE)) return null;
  return explainHeader(ctx);
}

export function explainHeader(ctx: Context): string {
  return JSON.stringify({
    outcome: ctx.decision.outcome, effective_outcome: ctx.effectiveOutcome, rule_id: ctx.decision.rule_id || null,
    mode: ctx.decision.mode, policy_version: ctx.decision.policy_version, action: ctx.action,
    amount_minor: ctx.amountMinor, currency: ctx.currency, data_classes: ctx.classes,
    request_redactions: [...ctx.requestRedactions], response_redact: [...ctx.responseRedact],
    response_block: [...ctx.responseBlock], signals: ctx.signals,
  });
}
