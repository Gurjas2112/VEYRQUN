/** POST /v1/chat/completions — OpenAI-compatible, governed. action = "llm.chat". */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import { classify, redact } from "../classify.js";
import { sha256 } from "../events.js";
import { costMinor, estimateTokens, messageText, priceFor, type ChatMessage, type ModelPrice, type ToolCall } from "../pricing.js";
import type { Context } from "../pipeline.js";
import { StreamScanner } from "../stream-scan.js";
import { callUpstream, UpstreamTimeout } from "../upstream.js";
import type { Deps } from "../app.js";
import { authenticate, sendGoverned } from "./common.js";

// Core fields are validated; other OpenAI parameters (temperature, tools, ...) pass through untouched.
const ChatBody = z.looseObject({
  model: z.string().min(1).max(128),
  // `content` may be absent (an assistant message that only carries tool_calls).
  messages: z.array(z.looseObject({ role: z.string().max(32), content: z.unknown().optional() })).min(1).max(512),
  stream: z.boolean().optional(),
  max_tokens: z.number().int().positive().max(1_000_000).optional(),
  stream_options: z.looseObject({ include_usage: z.boolean().optional() }).optional(),
});

type ChatChunk = {
  choices?: Array<{ delta?: { content?: string; tool_calls?: ToolCall[] }; index?: number; finish_reason?: string | null }>;
  usage?: Usage;
};
type Usage = { prompt_tokens?: number; completion_tokens?: number };

const redactText = (text: string, classes: Set<string>): string => redact(text, classify(text).spans, classes);

function redactMessages(messages: ChatMessage[], classes: Set<string>): ChatMessage[] {
  if (!classes.size) return messages;
  return messages.map((m) => {
    const out: ChatMessage = typeof m.content === "string" ? { ...m, content: redactText(m.content, classes) } : { ...m };
    if (Array.isArray(m.tool_calls)) {
      out.tool_calls = m.tool_calls.map((c) => c?.function?.arguments
        ? { ...c, function: { ...c.function, arguments: redactText(c.function.arguments, classes) } } : c);
    }
    return out;
  });
}

/** Scan one complete piece of model output. Returns the (redacted) text, or the blocked data class. */
function scanWhole(deps: Deps, ctx: Context, text: string): { text: string } | { blocked: string } {
  const scan = new StreamScanner(deps.cfg.STREAM_SCAN_HOLDBACK, ctx.responseRedact, ctx.responseBlock);
  const first = scan.push(text);
  if (first.kind === "abort") return { blocked: first.cls };
  const rest = scan.end();
  if (rest.kind === "abort") return { blocked: rest.cls };
  return { text: first.text + rest.text };
}

/** Tool-call arguments are released only once complete and scanned (a partial JSON string can't be judged). */
function scanToolCalls(deps: Deps, ctx: Context, calls: ToolCall[]): string | null {
  for (const c of calls) {
    if (!c.function?.arguments) continue;
    const v = scanWhole(deps, ctx, c.function.arguments);
    if ("blocked" in v) return v.blocked;
    c.function.arguments = v.text;
  }
  return null;
}

export function registerChat(app: FastifyInstance, deps: Deps): void {
  app.post("/v1/chat/completions", async (req: FastifyRequest, reply: FastifyReply) => {
    const t0 = process.hrtime.bigint();
    const identity = await authenticate(req, reply, deps);
    if (!identity) return reply;
    const parsed = ChatBody.safeParse(req.body);
    if (!parsed.success) return reply.code(400).send({ error: { type: "invalid_request", message: z.prettifyError(parsed.error) } });
    const body = parsed.data;
    const price = priceFor(deps.cfg.MODEL_PRICES, body.model);
    if (!price) return reply.code(400).send({ error: { type: "unknown_model", message: `no price configured for ${body.model}` } });

    const text = messageText(body.messages as ChatMessage[]);
    const estimate = costMinor(price, estimateTokens(text), body.max_tokens ?? deps.cfg.DEFAULT_MAX_TOKENS);
    const result = await deps.pipeline.govern({
      identity, action: "llm.chat", actionKind: "llm", amountMinor: estimate, text,
      approvalId: req.headers["x-veyrqun-approval"] as string | undefined,
    });
    if (result.kind === "respond") return sendGoverned(reply, result, deps);
    const ctx = result.ctx;
    const outgoing = { ...body, messages: redactMessages(body.messages as ChatMessage[], ctx.requestRedactions) };
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (deps.cfg.UPSTREAM_LLM_API_KEY) headers.authorization = `Bearer ${deps.cfg.UPSTREAM_LLM_API_KEY}`;
    const governMs = Number(process.hrtime.bigint() - t0) / 1e6;

    const abort = new AbortController();
    req.raw.on("close", () => { if (!reply.raw.writableEnded) abort.abort(); });
    const tUp = process.hrtime.bigint();
    let upstream;
    try {
      upstream = await callUpstream(deps.cfg.UPSTREAM_LLM_URLS, "/v1/chat/completions", {
        body: JSON.stringify(outgoing), headers, idempotent: true, signal: abort.signal,
        connectTimeoutMs: deps.cfg.UPSTREAM_CONNECT_TIMEOUT_MS, totalTimeoutMs: deps.cfg.UPSTREAM_TOTAL_TIMEOUT_MS,
      });
    } catch (err) {
      if (ctx.reservation) await deps.counters.release(ctx.reservation);
      const timeout = err instanceof UpstreamTimeout;
      await deps.pipeline.finish(ctx, { upstream_status: timeout ? 504 : 502 });
      return reply.code(timeout ? 504 : 502).header("x-veyrqun-decision-id", ctx.decisionId)
        .send({ error: { type: timeout ? "upstream_timeout" : "upstream_unavailable", decision_id: ctx.decisionId } });
    }
    const { res } = upstream;
    if (res.statusCode >= 400) {
      if (ctx.reservation) await deps.counters.release(ctx.reservation);
      const errBody = await res.body.text();
      await deps.pipeline.finish(ctx, { upstream_status: res.statusCode });
      return reply.code(res.statusCode).header("x-veyrqun-decision-id", ctx.decisionId).type("application/json").send(errBody);
    }

    if (body.stream) return streamResponse(reply, deps, ctx, res, tUp, governMs, price);

    const json = (await res.body.json()) as {
      choices?: Array<{ message?: { content?: string | null; tool_calls?: ToolCall[] } }>; usage?: Usage };
    const upstreamMs = Number(process.hrtime.bigint() - tUp) / 1e6;
    const tPost = process.hrtime.bigint();
    let blocked: string | null = null;
    for (const choice of json.choices ?? []) {
      const message = choice.message;
      if (!message) continue;
      if (typeof message.content === "string") {
        const v = scanWhole(deps, ctx, message.content);
        if ("blocked" in v) { blocked = v.blocked; break; }
        message.content = v.text;
      }
      if (Array.isArray(message.tool_calls) && (blocked = scanToolCalls(deps, ctx, message.tool_calls))) break;
    }
    await settleUsage(deps, ctx, json.usage, price);
    const postMs = Number(process.hrtime.bigint() - tPost) / 1e6;
    ctx.timings.upstream = upstreamMs;
    ctx.timings.overhead = governMs + postMs;
    deps.metrics.overhead.observe({ route: "chat" }, (governMs + postMs) / 1000);
    deps.metrics.upstream.observe({ route: "chat", status: String(res.statusCode) }, upstreamMs / 1000);
    if (blocked) {
      deps.metrics.streamAborts.inc({ data_class: blocked });
      await deps.pipeline.finish(ctx, { upstream_status: res.statusCode, stream_aborted: true });
      return reply.code(403).header("x-veyrqun-decision-id", ctx.decisionId)
        .send({ error: { type: "response_policy_violation", data_class: blocked, decision_id: ctx.decisionId } });
    }
    await deps.pipeline.finish(ctx, { upstream_status: res.statusCode, response_sha256: sha256(JSON.stringify(json)) });
    return reply.header("x-veyrqun-decision-id", ctx.decisionId)
      .header("server-timing", `gateway;dur=${(governMs + postMs).toFixed(2)}, upstream;dur=${upstreamMs.toFixed(2)}`)
      .send(json);
  });
}

/** Replace the pre-call estimate with the real cost from `usage`, in the counters and in the event. */
async function settleUsage(deps: Deps, ctx: Context, usage: Usage | undefined, price: ModelPrice): Promise<void> {
  if (!usage || usage.prompt_tokens === undefined || usage.completion_tokens === undefined) return;
  const actual = costMinor(price, usage.prompt_tokens, usage.completion_tokens);
  if (ctx.reservation) await deps.counters.adjust(ctx.reservation, actual);
  ctx.timings.estimate_minor = ctx.amountMinor;
  ctx.amountMinor = actual;
}

async function streamResponse(reply: FastifyReply, deps: Deps, ctx: Context,
  res: { body: AsyncIterable<Buffer>; statusCode: number }, tUp: bigint, governMs: number, price: ModelPrice):
  Promise<FastifyReply> {
  reply.hijack();
  const out = reply.raw;
  out.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no",
    "x-veyrqun-decision-id": ctx.decisionId,
  });
  const write = async (s: string) => {
    if (!out.write(s)) await new Promise<void>((resolve) => out.once("drain", resolve)); // backpressure
  };
  const scanner = new StreamScanner(deps.cfg.STREAM_SCAN_HOLDBACK, ctx.responseRedact, ctx.responseBlock);
  let template: ChatChunk | null = null;
  let aborted: string | null = null;
  let usage: Usage | undefined;
  let pending = "";
  let scanMs = 0;

  const toolCalls = new Map<number, ToolCall>();
  let finishReason: string | null = null;

  const emitChoice = async (choice: NonNullable<ChatChunk["choices"]>[number]) => {
    const chunk: ChatChunk = template ? structuredClone(template) : {};
    delete chunk.usage;
    chunk.choices = [choice];
    await write(`data: ${JSON.stringify(chunk)}\n\n`);
  };
  const emitText = async (text: string) => {
    if (text) await emitChoice({ index: 0, delta: { content: text } });
  };

  outer: for await (const raw of res.body) {
    pending += raw.toString("utf8");
    let idx;
    while ((idx = pending.indexOf("\n\n")) >= 0) {
      const event = pending.slice(0, idx);
      pending = pending.slice(idx + 2);
      for (const line of event.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") break outer;
        let chunk: ChatChunk;
        try { chunk = JSON.parse(data) as ChatChunk; } catch { continue; }
        if (chunk.usage) usage = chunk.usage;
        template ??= chunk;
        const choice = chunk.choices?.[0];
        if (choice?.finish_reason) finishReason = choice.finish_reason;
        for (const d of choice?.delta?.tool_calls ?? []) {
          const i = d.index ?? 0;
          const acc = toolCalls.get(i) ?? { index: i, function: { name: "", arguments: "" } };
          if (d.id) acc.id = d.id;
          if (d.type) acc.type = d.type;
          if (d.function?.name) acc.function!.name = (acc.function!.name ?? "") + d.function.name;
          if (d.function?.arguments) acc.function!.arguments = (acc.function!.arguments ?? "") + d.function.arguments;
          toolCalls.set(i, acc);
        }
        const piece = choice?.delta?.content ?? "";
        const t = process.hrtime.bigint();
        const v = scanner.push(piece);
        scanMs += Number(process.hrtime.bigint() - t) / 1e6;
        if (v.kind === "abort") { aborted = v.cls; break outer; }
        await emitText(v.text);
      }
    }
  }
  if (!aborted) {
    const v = scanner.end();
    if (v.kind === "abort") aborted = v.cls;
    else await emitText(v.text);
  }
  if (!aborted && toolCalls.size) {
    const calls = [...toolCalls.values()].sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
    const t = process.hrtime.bigint();
    aborted = scanToolCalls(deps, ctx, calls);
    scanMs += Number(process.hrtime.bigint() - t) / 1e6;
    if (!aborted) await emitChoice({ index: 0, delta: { tool_calls: calls } });
  }
  if (!aborted && finishReason) await emitChoice({ index: 0, delta: {}, finish_reason: finishReason });
  if (aborted) {
    deps.metrics.streamAborts.inc({ data_class: aborted });
    await write(`event: error\ndata: ${JSON.stringify({ error: { type: "response_policy_violation", data_class: aborted,
      decision_id: ctx.decisionId } })}\n\n`);
  } else {
    if (usage) await write(`data: ${JSON.stringify({ ...(template ?? {}), choices: [], usage })}\n\n`);
    await write("data: [DONE]\n\n");
  }
  out.end();
  await settleUsage(deps, ctx, usage, price);
  const upstreamMs = Number(process.hrtime.bigint() - tUp) / 1e6;
  ctx.timings.upstream = upstreamMs;
  ctx.timings.overhead = governMs + scanMs;
  deps.metrics.overhead.observe({ route: "chat_stream" }, (governMs + scanMs) / 1000);
  await deps.pipeline.finish(ctx, { upstream_status: res.statusCode, stream_aborted: Boolean(aborted) });
  return reply;
}
