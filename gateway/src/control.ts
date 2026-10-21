/**
 * Control channel: kill switch and policy changes.
 * Primary: Redis pub/sub `veyrqun:control` (sub-second). Backstop: poll `vq:fleet:{ws}` every FLEET_POLL_MS,
 * so a dropped pub/sub message still takes effect within a second.
 */
import type { Redis } from "ioredis";
import type { BundleStore } from "./bundle.js";

interface FleetState {
  stopped: boolean;
  version: number;
  changedAt: number;
}

export class ControlChannel {
  private fleet = new Map<string, FleetState>();
  private timer: NodeJS.Timeout | null = null;
  private bundleTimer: NodeJS.Timeout | null = null;
  onKill?: (ws: string, stopped: boolean, via: "pubsub" | "poll") => void;

  constructor(
    private readonly redis: Redis,
    private readonly subscriber: Redis,
    private readonly bundles: BundleStore,
    private readonly opts: { fleetPollMs: number; bundlePollMs: number },
  ) {}

  isStopped(ws: string): boolean {
    return this.fleet.get(ws)?.stopped ?? false;
  }

  private apply(ws: string, stopped: boolean, version: number, via: "pubsub" | "poll"): void {
    const prev = this.fleet.get(ws);
    // Only strictly newer state wins. A delayed, older "resume" must never un-stop a fleet that a
    // newer message stopped (pub/sub and polling can deliver out of order).
    if (prev && version <= prev.version) return;
    this.fleet.set(ws, { stopped, version, changedAt: Date.now() });
    if (!prev || prev.stopped !== stopped) this.onKill?.(ws, stopped, via);
  }

  async start(): Promise<void> {
    // If Redis is down at startup the gateway still serves: counters fail closed, and ioredis
    // re-subscribes automatically once the connection comes back.
    void this.subscriber.subscribe("veyrqun:control").catch(() => undefined);
    this.subscriber.on("message", (_channel, raw) => {
      let msg: { type?: string; workspace?: string; stopped?: boolean; version?: number };
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      if (msg.type === "fleet" && msg.workspace) {
        this.apply(msg.workspace, Boolean(msg.stopped), Number(msg.version ?? 0), "pubsub");
      }
      // Any policy/status change: refetch bundles now instead of waiting for the next poll.
      for (const ws of this.bundles.workspaces()) {
        if (!msg.workspace || msg.workspace === ws) void this.bundles.refresh(ws).catch(() => undefined);
      }
    });
    this.timer = setInterval(() => void this.pollFleet(), this.opts.fleetPollMs);
    this.bundleTimer = setInterval(() => {
      for (const ws of this.bundles.workspaces()) void this.bundles.refresh(ws).catch(() => undefined);
    }, this.opts.bundlePollMs);
  }

  async pollFleet(): Promise<void> {
    const workspaces = this.bundles.workspaces();
    if (!workspaces.length) return;
    try {
      const values = await this.redis.mget(...workspaces.map((ws) => `vq:fleet:${ws}`));
      values.forEach((raw, i) => {
        if (!raw) return;
        const v = JSON.parse(raw) as { stopped: boolean; version: number };
        this.apply(workspaces[i]!, v.stopped, v.version, "poll");
      });
    } catch {
      // Redis down: counters fail closed anyway; the bundle's fleet flag still applies.
    }
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    if (this.bundleTimer) clearInterval(this.bundleTimer);
  }
}
