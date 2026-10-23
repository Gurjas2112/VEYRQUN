/**
 * Deterministic, offline OpenAI-compatible mock LLM (CI, demos, load tests). No API keys, no network.
 * Triggers in the last user message:
 *   LEAK_CARD (or "test card number") -> answer contains a Luhn-valid card number split across stream chunks
 *   LEAK_EMAIL -> answer contains an email address
 *   SLOW       -> waits 3 s before answering (timeout tests)
 *   FAIL500    -> HTTP 500 (failover tests)
 */
import Fastify, { type FastifyInstance } from "fastify";

const CARD = "4111 1111 1111 1111";

function answerFor(prompt: string): string {
  if (prompt.includes("LEAK_CARD") || /test card number/i.test(prompt)) return `Sure, the card on file is ${CARD}, expiring soon.`;
  if (prompt.includes("LEAK_EMAIL")) return "You can reach the customer at jane.doe@example.com today.";
  return `Acknowledged: ${prompt.slice(0, 80)}`;
}

type Msg = { role: string; content: string | null; tool_calls?: unknown[] };
type ToolDef = { type: string; function: { name: string } };

/**
 * Deterministic "planning" when the request offers tools: every `CALL <tool> {json}` directive in the last user
 * message becomes a tool call (a model following instructions, or a hijacked one following injected text).
 * After tool results arrive, it summarises them. Tools not offered in the request are ignored.
 */
export function planToolCalls(messages: Msg[], tools: ToolDef[] | undefined): Array<{ name: string; arguments: string }> {
  if (!tools?.length || messages.at(-1)?.role !== "user") return [];
  const offered = new Set(tools.map((t) => t.function?.name));
  const text = messages.at(-1)?.content ?? "";
  const calls: Array<{ name: string; arguments: string }> = [];
  for (const m of text.matchAll(/CALL\s+([a-z_][a-z0-9_]*)\s*(\{[^{}]*\})?/gi)) {
    const name = m[1]!;
    if (!offered.has(name)) continue;
    let args = "{}";
    try { args = JSON.stringify(JSON.parse(m[2] ?? "{}")); } catch { /* keep {} */ }
    // The model itself "generates" PII into the arguments (it was not in the prompt), to test response scanning.
    calls.push({ name, arguments: args.replaceAll("LEAK_CARD", CARD).replaceAll("LEAK_EMAIL", "jane.doe@example.com") });
  }
  return calls;
}

function summarise(messages: Msg[]): string {
  const results = [];
  for (let i = messages.length - 1; i >= 0 && messages[i]!.role === "tool"; i--) results.unshift(String(messages[i]!.content ?? ""));
  return `Done. ${results.map((r) => r.slice(0, 120)).join(" | ")}`;
}

export function buildMockLlm(opts: { latencyMs?: number; name?: string } = {}): FastifyInstance {
  const app = Fastify({ logger: false });
  app.post("/v1/chat/completions", async (req, reply) => {
    const body = req.body as { model: string; messages: Msg[]; stream?: boolean; tools?: ToolDef[];
      stream_options?: { include_usage?: boolean } };
    const last = String([...body.messages].reverse().find((m) => m.role === "user")?.content ?? "");
    if (last.includes("FAIL500")) return reply.code(500).send({ error: { message: "mock upstream failure" } });
    if (last.includes("SLOW")) await new Promise((r) => setTimeout(r, 3_000));
    if (opts.latencyMs) await new Promise((r) => setTimeout(r, opts.latencyMs));
    const planned = planToolCalls(body.messages, body.tools);
    const toolCalls = planned.map((c, i) => ({ index: i, id: `call_${i + 1}`, type: "function",
      function: { name: c.name, arguments: c.arguments } }));
    const answer = toolCalls.length ? "" : body.messages.at(-1)?.role === "tool" ? summarise(body.messages) : answerFor(last);
    const finish = toolCalls.length ? "tool_calls" : "stop";
    const outTokens = Math.ceil((answer.length + JSON.stringify(planned).length) / 4);
    const usage = { prompt_tokens: Math.ceil(JSON.stringify(body.messages).length / 4), completion_tokens: outTokens };
    const id = "chatcmpl-mock";
    if (!body.stream) {
      const message = toolCalls.length
        ? { role: "assistant", content: null, tool_calls: toolCalls.map(({ index: _i, ...c }) => c) }
        : { role: "assistant", content: answer };
      return { id, object: "chat.completion", model: body.model, served_by: opts.name ?? "mock",
        choices: [{ index: 0, message, finish_reason: finish }], usage };
    }
    reply.hijack();
    reply.raw.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
    const send = (choice: object) => reply.raw.write(`data: ${JSON.stringify({ id, object: "chat.completion.chunk",
      model: body.model, choices: [choice] })}\n\n`);
    // Tool-call arguments arrive in 5-character fragments, as real providers stream them.
    for (const c of toolCalls) {
      send({ index: 0, delta: { tool_calls: [{ index: c.index, id: c.id, type: "function", function: { name: c.function.name, arguments: "" } }] } });
      for (let i = 0; i < c.function.arguments.length; i += 5) {
        send({ index: 0, delta: { tool_calls: [{ index: c.index, function: { arguments: c.function.arguments.slice(i, i + 5) } }] } });
        await new Promise((r) => setTimeout(r, 1));
      }
    }
    // 7-character chunks guarantee that a card number spans several chunks.
    for (let i = 0; i < answer.length; i += 7) {
      send({ index: 0, delta: { content: answer.slice(i, i + 7) } });
      await new Promise((r) => setTimeout(r, 2));
    }
    send({ index: 0, delta: {}, finish_reason: finish });
    if (body.stream_options?.include_usage) {
      reply.raw.write(`data: ${JSON.stringify({ id, object: "chat.completion.chunk", choices: [], usage })}\n\n`);
    }
    reply.raw.write("data: [DONE]\n\n");
    reply.raw.end();
    return reply;
  });
  return app;
}

if (import.meta.url === `file://${process.argv[1]?.replace(/\\/g, "/")}` || process.argv[1]?.endsWith("llm.ts")) {
  const port = Number(process.env.PORT ?? 9101);
  await buildMockLlm({ latencyMs: Number(process.env.MOCK_LATENCY_MS ?? 0) }).listen({ port, host: "0.0.0.0" });
  console.log(`mock LLM on :${port}`);
}
