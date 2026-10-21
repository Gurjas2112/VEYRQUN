/**
 * Upstream calls with per-attempt connect/headers timeouts, a total deadline, and tiered failover.
 * Only idempotent calls are retried on another upstream, and only before any response bytes arrive.
 * Tool calls (side effects in a bank core) are never retried.
 */
import { Agent, request, type Dispatcher } from "undici";

export class UpstreamTimeout extends Error {}
export class UpstreamUnavailable extends Error {}

export interface UpstreamOptions {
  method?: "POST" | "GET";
  body?: string;
  headers?: Record<string, string>;
  idempotent: boolean;
  connectTimeoutMs: number;
  totalTimeoutMs: number;
  signal?: AbortSignal;
}

const agents = new Map<number, Agent>();
const dispatcher = (connectTimeoutMs: number): Agent => {
  let a = agents.get(connectTimeoutMs);
  if (!a) {
    a = new Agent({ connect: { timeout: connectTimeoutMs }, keepAliveTimeout: 10_000, connections: 128 });
    agents.set(connectTimeoutMs, a);
  }
  return a;
};

export async function callUpstream(urls: string[], path: string, o: UpstreamOptions):
  Promise<{ res: Dispatcher.ResponseData; url: string; attempts: number }> {
  const deadline = AbortSignal.timeout(o.totalTimeoutMs);
  const signal = o.signal ? AbortSignal.any([deadline, o.signal]) : deadline;
  const candidates = o.idempotent ? urls : urls.slice(0, 1);
  let lastErr: unknown;
  let attempts = 0;
  for (const base of candidates) {
    attempts++;
    try {
      const res = await request(`${base.replace(/\/$/, "")}${path}`, {
        method: o.method ?? "POST",
        body: o.body,
        headers: o.headers,
        signal,
        dispatcher: dispatcher(o.connectTimeoutMs),
        headersTimeout: o.totalTimeoutMs,
        bodyTimeout: o.totalTimeoutMs,
      });
      if (res.statusCode >= 500 && o.idempotent && attempts < candidates.length) {
        await res.body.dump();
        lastErr = new UpstreamUnavailable(`upstream ${base} returned ${res.statusCode}`);
        continue;
      }
      return { res, url: base, attempts };
    } catch (err) {
      lastErr = err;
      if (deadline.aborted) throw new UpstreamTimeout(`upstream timed out after ${o.totalTimeoutMs} ms`);
      if (o.signal?.aborted) throw err;
      const name = (err as Error).name;
      if (name === "HeadersTimeoutError" || name === "BodyTimeoutError") {
        if (!o.idempotent) throw new UpstreamTimeout("upstream timed out");
        continue;
      }
      if (!o.idempotent) throw new UpstreamUnavailable((err as Error).message);
    }
  }
  if (lastErr instanceof UpstreamTimeout) throw lastErr;
  throw new UpstreamUnavailable(lastErr instanceof Error ? lastErr.message : "all upstreams failed");
}
