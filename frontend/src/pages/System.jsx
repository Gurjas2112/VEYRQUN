import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Card, Code, KeyValue, Pill, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useCan, useMe } from "../lib/auth";
import { ago, time } from "../lib/format";

export default function System() {
  const { data: me } = useMe();
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const [confirm, dialog] = useConfirm();
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: () => api("/fleet/status"), refetchInterval: 4000 });
  const gateway = useQuery({
    queryKey: ["gateway"], refetchInterval: 5000,
    queryFn: async () => { const r = await fetch("/gateway-status"); return { status: r.status, ...(await r.json().catch(() => ({}))) }; },
  });
  const pipeline = useQuery({ queryKey: ["pipeline"], queryFn: () => api("/audit/pipeline"), enabled: can("audit:verify"), refetchInterval: 10000 });

  const toggle = useMutation({
    mutationFn: (stop) => api(stop ? "/emergency-stop" : "/resume-all", { method: "POST" }),
    onSuccess: (r) => { toast(r.message, "ok"); qc.invalidateQueries(); },
    onError: (e) => toast(e.status === 401 ? "Recent sign-in required for the kill switch. Sign in again." : e.message, "bad"),
  });

  async function killSwitch(stop) {
    const res = await confirm(stop
      ? { title: "Stop every agent?", body: "All gateways refuse every agent request in this workspace until released.", typeToConfirm: "STOP", danger: true, confirmLabel: "Stop the fleet" }
      : { title: "Release the kill switch?", body: "Agents go back to normal policy checks. In production this needs a second person.", confirmLabel: "Release" });
    if (res) toggle.mutate(stop);
  }

  const ws = me?.workspace_id;
  const rev = gateway.data?.bundle_revisions?.[ws];
  return (
    <div className="space-y-6">
      {dialog}
      <div>
        <h1 className="text-xl font-semibold">System</h1>
        <p className="text-sm text-muted">Kill switch, gateway health, the audit pipeline and your session.</p>
      </div>

      <Card title="Fleet kill switch">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-lg font-semibold">
              {fleet.data?.fleet_stopped ? <><span className="size-2.5 rounded-full bg-bad" />Stopped</> : <><span className="size-2.5 rounded-full bg-ok" />Running</>}
            </div>
            <div className="text-sm text-muted">
              {fleet.data && `${fleet.data.agents_by_status.active} active · ${fleet.data.agents_by_status.stopped} stopped · ${fleet.data.agents_by_status.revoked} revoked`}
            </div>
            <div className="mt-1 text-xs text-faint">Gateways apply it within about a second (pub/sub, with a 1-second poll as backup).</div>
          </div>
          {can("fleet:stop") && !fleet.data?.fleet_stopped && <Button variant="danger" onClick={() => killSwitch(true)}>Stop all agents</Button>}
          {can("fleet:release") && fleet.data?.fleet_stopped && <Button variant="primary" onClick={() => killSwitch(false)}>Release</Button>}
        </div>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Gateway">
          <KeyValue items={[
            ["Ready", gateway.data ? (gateway.data.ready ? <Pill key="r" tone="ok">ready</Pill> : <Pill key="n" tone="bad">not ready ({gateway.data.status})</Pill>) : "…"],
            ["Redis", gateway.data?.checks?.redis ?? "…"],
            ["Policy bundle", rev != null ? `revision ${rev} loaded${gateway.data?.checks?.[`bundle:${ws}`] ? ` · ${gateway.data.checks[`bundle:${ws}`]}` : ""}` : "not loaded yet (loads on first request)"],
          ]} />
          <p className="mt-3 text-xs text-faint">If the bundle goes stale or Redis is unreachable, the gateway refuses requests (fail closed) rather than guessing.</p>
        </Card>
        <Card title="Audit pipeline">
          {pipeline.data ? (
            <KeyValue items={[
              ["Backlog", pipeline.data.backlog ?? 0], ["Unacknowledged", pipeline.data.pending ?? 0],
              ["Dead letters", pipeline.data.dead_letters],
              ["Writer", pipeline.data.writer ? `${pipeline.data.writer.consumer} · heartbeat ${pipeline.data.writer.heartbeat_age_s}s ago` : "no heartbeat (drain-on-read active)"],
            ]} />
          ) : <p className="text-sm text-muted">Needs the auditor or security operator role.</p>}
        </Card>
      </div>

      <Card title="Your session">
        <KeyValue items={[
          ["Signed in as", me?.demo?.guest ? "Anonymous demo guest" : me?.email],
          ["Workspace", <Code key="w">{ws}</Code>],
          ["Roles", (me?.roles ?? []).join(", ")],
          ["Multi-factor", me?.mfa ? "yes" : me?.demo?.guest ? "not used (sandbox demo exception)" : "no"],
          me?.demo?.expires_at && ["Demo closes", `${time(me.demo.expires_at)}`],
          ["Permissions", <span key="p" className="text-xs text-muted">{(me?.permissions ?? []).join(" · ")}</span>],
        ]} />
        {gateway.dataUpdatedAt ? <p className="mt-3 text-xs text-faint">Health refreshed {ago(new Date(gateway.dataUpdatedAt).toISOString())}.</p> : null}
      </Card>
    </div>
  );
}
