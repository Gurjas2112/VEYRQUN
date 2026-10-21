/**
 * Policy bundles: fetched from the control plane with If-None-Match, verified (Ed25519 detached signature
 * against the control plane's JWKS) before use, and hot-swapped atomically per workspace. The last good
 * bundle keeps serving while the control plane is unreachable, until BUNDLE_MAX_STALENESS_S runs out;
 * after that the gateway fails closed (503).
 */
import { createPublicKey, verify as verifySig } from "node:crypto";
import { gunzipSync } from "node:zlib";
import { loadPolicy, type LoadedPolicy } from "@open-policy-agent/opa-wasm";
import { request } from "undici";

export interface AgentPolicyDoc {
  status: string;
  mode: "observe" | "enforce";
  policy_version: number;
  allowed_actions: string[];
  blocked_actions: string[];
  redact_data_classes: string[];
  block_data_classes: string[];
  currency?: string;
  timezone?: string;
}

export interface PolicyData {
  agents: Record<string, AgentPolicyDoc>;
  fleet: { stopped: boolean; version?: number };
  settings?: { tool_amounts?: Record<string, string>; fail_open_safe_actions?: string[]; injection_block_threshold?: number };
  revision?: number;
}

export interface Decision {
  outcome: "allow" | "block" | "redact" | "require_approval";
  rule_id: string | null;
  redactions: string[];
  limits: { spend_cap_minor?: number; rate_limit_per_minute?: number | null; action_daily_cap_minor?: number | null };
  mode: "observe" | "enforce";
  policy_version: number;
  fail_open_safe?: boolean;
}

export interface LoadedBundle {
  workspaceId: string;
  etag: string;
  revision: number;
  data: PolicyData;
  policy: LoadedPolicy;
  fetchedAt: number;
}

export class BundleError extends Error {}

/** Minimal POSIX ustar reader (the bundle is produced by our own deterministic writer). */
export function untar(buf: Buffer): Map<string, Buffer> {
  const files = new Map<string, Buffer>();
  let off = 0;
  while (off + 512 <= buf.length) {
    const header = buf.subarray(off, off + 512);
    if (header.every((b) => b === 0)) break;
    const name = header.subarray(0, 100).toString("utf8").replace(/\0.*$/s, "");
    const prefix = header.subarray(345, 500).toString("utf8").replace(/\0.*$/s, "");
    const size = parseInt(header.subarray(124, 136).toString("utf8").replace(/\0.*$/s, "").trim() || "0", 8);
    const full = (prefix ? `${prefix}/${name}` : name).replace(/^\.?\//, "");
    files.set(full, buf.subarray(off + 512, off + 512 + size));
    off += 512 + Math.ceil(size / 512) * 512;
  }
  return files;
}

type Jwk = { kid: string; kty: string; crv?: string; x?: string };

export class BundleStore {
  private bundles = new Map<string, LoadedBundle>();
  private inflight = new Map<string, Promise<LoadedBundle>>();
  private lastSuccess = new Map<string, number>();
  private jwks: { at: number; keys: Jwk[] } | null = null;

  constructor(
    private readonly opts: {
      controlPlaneUrl: string;
      jwksUrl: string;
      serviceToken: string;
      maxStalenessS: number;
      now?: () => number;
    },
  ) {}

  private now(): number {
    return this.opts.now ? this.opts.now() : Date.now();
  }

  workspaces(): string[] {
    return [...this.bundles.keys()];
  }

  ageSeconds(ws: string): number | null {
    const at = this.lastSuccess.get(ws);
    return at === undefined ? null : (this.now() - at) / 1000;
  }

  /** The current bundle for a workspace, fetching it on first use. Throws if missing or too stale. */
  async get(ws: string): Promise<LoadedBundle> {
    let b = this.bundles.get(ws);
    if (!b) b = await this.refresh(ws);
    const age = this.ageSeconds(ws) ?? Infinity;
    if (age > this.opts.maxStalenessS) {
      throw new BundleError(`policy bundle for ${ws} is stale (${Math.round(age)}s > ${this.opts.maxStalenessS}s)`);
    }
    return b;
  }

  /** Conditional fetch; keeps the last good bundle on any failure. Concurrent callers share one fetch. */
  refresh(ws: string): Promise<LoadedBundle> {
    const existing = this.inflight.get(ws);
    if (existing) return existing;
    const p = this.fetchOnce(ws).finally(() => this.inflight.delete(ws));
    this.inflight.set(ws, p);
    return p;
  }

  private async fetchOnce(ws: string): Promise<LoadedBundle> {
    const current = this.bundles.get(ws);
    const url = `${this.opts.controlPlaneUrl.replace(/\/$/, "")}/bundles/current?workspace_id=${encodeURIComponent(ws)}`;
    const headers: Record<string, string> = { authorization: `Bearer ${this.opts.serviceToken}` };
    if (current) headers["if-none-match"] = `"${current.etag}"`;
    let res;
    try {
      res = await request(url, { headers, headersTimeout: 3_000, bodyTimeout: 10_000 });
    } catch (err) {
      if (current) return current;
      throw new BundleError(`control plane unreachable: ${(err as Error).message}`);
    }
    if (res.statusCode === 304 && current) {
      await res.body.dump();
      this.lastSuccess.set(ws, this.now());
      return current;
    }
    if (res.statusCode !== 200) {
      await res.body.dump();
      if (current) return current;
      throw new BundleError(`bundle fetch failed with HTTP ${res.statusCode}`);
    }
    const body = Buffer.from(await res.body.arrayBuffer());
    const sig = String(res.headers["x-veyrqun-signature"] ?? "");
    const kid = String(res.headers["x-veyrqun-signature-kid"] ?? "");
    await this.verify(body, sig, kid);
    const files = untar(gunzipSync(body));
    const wasm = files.get("policy.wasm");
    const dataRaw = files.get("data.json");
    if (!wasm || !dataRaw) throw new BundleError("bundle is missing policy.wasm or data.json");
    const data = JSON.parse(dataRaw.toString("utf8")) as { policy_data: PolicyData };
    const policy = await loadPolicy(wasm);
    policy.setData({ policy_data: data.policy_data });
    const loaded: LoadedBundle = {
      workspaceId: ws,
      etag: String(res.headers.etag ?? "").replace(/"/g, ""),
      revision: Number(res.headers["x-veyrqun-bundle-revision"] ?? 0),
      data: data.policy_data,
      policy,
      fetchedAt: this.now(),
    };
    this.bundles.set(ws, loaded); // atomic swap: requests see either the old or the new bundle, never half of one
    this.lastSuccess.set(ws, this.now());
    return loaded;
  }

  private async keys(force: boolean): Promise<Jwk[]> {
    if (force || !this.jwks || this.now() - this.jwks.at > 60_000) {
      const res = await request(this.opts.jwksUrl, { headersTimeout: 3_000 });
      const doc = (await res.body.json()) as { keys: Jwk[] };
      this.jwks = { at: this.now(), keys: doc.keys };
    }
    return this.jwks.keys;
  }

  private async verify(body: Buffer, sigB64u: string, kid: string): Promise<void> {
    if (!sigB64u || !kid) throw new BundleError("bundle is not signed");
    for (const force of [false, true]) {
      const jwk = (await this.keys(force)).find((k) => k.kid === kid);
      if (!jwk) continue;
      const key = createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: jwk.x! }, format: "jwk" });
      if (!verifySig(null, body, key, Buffer.from(sigB64u, "base64url"))) {
        throw new BundleError("bundle signature is invalid");
      }
      return;
    }
    throw new BundleError(`bundle signing key ${kid} not found in JWKS`);
  }

  evaluate(bundle: LoadedBundle, input: Record<string, unknown>): Decision {
    const result = bundle.policy.evaluate(input) as Array<{ result?: Decision }>;
    const d = result[0]?.result;
    if (!d || typeof d.outcome !== "string") {
      return { outcome: "block", rule_id: "policy_undefined", redactions: [], limits: {}, mode: "enforce", policy_version: 0 };
    }
    return d;
  }
}
