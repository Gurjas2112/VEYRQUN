/** POST /v1/chat/completions — OpenAI-compatible, governed. action = "llm.chat". */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { z } from "zod";
import { classify, redact } from "../classify.js";
import { sha256 } from "../events.js";
import { costMinor, estimateTokens, messageText, priceFor, type ChatMessage, type ModelPrice } from "../pricing.js";
import type { Context } from "../pipeline.js";
import { StreamScanner } from "../stream-scan.js";
import { callUpstream, UpstreamTimeout } from "../upstream.js";
import type { Deps } from "../app.js";
import { authenticate, sendGoverned } from "./common.js";

// Core fields are validated; other OpenAI parameters (temperature, tools, ...) pass through untouched.
const ChatBody = z.looseObject({
  model: z.string().min(1).max(128),
  messages: z.array(z.looseObject({ role: z.string().max(32), content: z.unknown() })).min(1).max(512),
  stream: z.boolean().optional(),
  max_tokens: z.number().int().positive().max(1_000_000).optional(),
  stream_options: z.looseObject({ include_usage: z.boolean().optional() }).optional(),
});

type ChatChunk = { choices?: Array<{ delta?: { content?: string }; index?: number }>; usage?: Usage };
type Usage = { prompt_tokens?: number; completion_tokens?: number };

function redactMessages(messages: ChatMessage[], classes: Set<string>): ChatMessage[] {
  if (!classes.size) return messages;
  return messages.map((m) => {
    if (typeof m.content !== "string") return m;
    const { spans } = classify(m.content);
    return { ...m, content: redact(m.content, spans, classes) };
  });
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

    const json = (await res.body.json()) as { choices?: Array<{ message?: { content?: string } }>; usage?: Usage };
    const upstreamMs = Number(process.hrtime.bigint() - tUp) / 1e6;
    const tPost = process.hrtime.bigint();
    let blocked: string | null = null;
    for (const choice of json.choices ?? []) {
      const content = choice.message?.content;
      if (typeof content !== "string") continue;
      const scan = new StreamScanner(deps.cfg.STREAM_SCAN_HOLDBACK, ctx.responseRedact, ctx.responseBlock);
      scan.push(content);
      const v = scan.end();
      if (v.kind === "abort") { blocked = v.cls; break; }
      choice.message!.content = v.text;
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

  const emitText = async (text: string) => {
    if (!text) return;
    const chunk: ChatChunk = template ? structuredClone(template) : { choices: [{ index: 0, delta: {} }] };
    delete chunk.usage;
    chunk.choices = [{ index: 0, delta: { content: text } }];
    await write(`data: ${JSON.stringify(chunk)}\n\n`);
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
        const piece = chunk.choices?.[0]?.delta?.content ?? "";
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
