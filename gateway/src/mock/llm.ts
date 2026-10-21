/**
 * Deterministic, offline OpenAI-compatible mock LLM (CI, demos, load tests). No API keys, no network.
 * Triggers in the last user message:
 *   LEAK_CARD  -> answer contains a Luhn-valid card number split across stream chunks
 *   LEAK_EMAIL -> answer contains an email address
 *   SLOW       -> waits 3 s before answering (timeout tests)
 *   FAIL500    -> HTTP 500 (failover tests)
 */
import Fastify, { type FastifyInstance } from "fastify";

const CARD = "4111 1111 1111 1111";

function answerFor(prompt: string): string {
  if (prompt.includes("LEAK_CARD")) return `Sure, the card on file is ${CARD}, expiring soon.`;
  if (prompt.includes("LEAK_EMAIL")) return "You can reach the customer at jane.doe@example.com today.";
  return `Acknowledged: ${prompt.slice(0, 80)}`;
}

export function buildMockLlm(opts: { latencyMs?: number; name?: string } = {}): FastifyInstance {
  const app = Fastify({ logger: false });
  app.post("/v1/chat/completions", async (req, reply) => {
    const body = req.body as { model: string; messages: Array<{ role: string; content: string }>; stream?: boolean;
      stream_options?: { include_usage?: boolean } };
    const last = [...body.messages].reverse().find((m) => m.role === "user")?.content ?? "";
    if (last.includes("FAIL500")) return reply.code(500).send({ error: { message: "mock upstream failure" } });
    if (last.includes("SLOW")) await new Promise((r) => setTimeout(r, 3_000));
    if (opts.latencyMs) await new Promise((r) => setTimeout(r, opts.latencyMs));
    const answer = answerFor(last);
    const usage = { prompt_tokens: Math.ceil(JSON.stringify(body.messages).length / 4), completion_tokens: Math.ceil(answer.length / 4) };
    const id = "chatcmpl-mock";
    if (!body.stream) {
      return { id, object: "chat.completion", model: body.model, served_by: opts.name ?? "mock",
        choices: [{ index: 0, message: { role: "assistant", content: answer }, finish_reason: "stop" }], usage };
    }
    reply.hijack();
    reply.raw.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" });
    // 7-character chunks guarantee that a card number spans several chunks.
    for (let i = 0; i < answer.length; i += 7) {
      const chunk = { id, object: "chat.completion.chunk", model: body.model,
        choices: [{ index: 0, delta: { content: answer.slice(i, i + 7) } }] };
      reply.raw.write(`data: ${JSON.stringify(chunk)}\n\n`);
      await new Promise((r) => setTimeout(r, 2));
    }
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
