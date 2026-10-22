import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { Button, Card, Code, Dialog, Empty, Input, KeyValue, OutcomePill, Pill, Select, useToast } from "../components/ui";
import { api, download } from "../lib/api";
import { useCan } from "../lib/auth";
import { money, time } from "../lib/format";

function EntryDialog({ id, onClose }) {
  const entry = useQuery({ queryKey: ["entry", id], queryFn: () => api(`/audit/${id}`), enabled: !!id });
  const check = useQuery({ queryKey: ["entry-verify", id], queryFn: () => api(`/audit/${id}/verify`), enabled: !!id });
  const e = entry.data;
  return (
    <Dialog open={!!id} onClose={onClose} title={e ? `Ledger entry #${e.seq}` : "Ledger entry"} wide>
      {e && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <OutcomePill outcome={e.outcome} />
            {e.rule_id && <Code>{e.rule_id}</Code>}
            {check.data && (check.data.valid ? <Pill tone="ok">✓ hash and signature verified</Pill> : <Pill tone="bad">✗ verification failed</Pill>)}
          </div>
          <KeyValue items={[
            ["When", time(e.ts)], ["Agent", e.agent_id], ["Action", e.action_type],
            ["Amount", money(e.amount, e.currency)], ["Actor", <Code key="a">{e.actor}</Code>],
            ["Policy", `v${e.policy_version} · ${e.mode}`], ["Decision id", <Code key="d">{e.decision_id}</Code>],
          ]} />
          {Object.keys(e.details ?? {}).length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-muted">Recorded details (hashed into the entry)</div>
              <pre className="max-h-48 overflow-auto rounded-lg bg-raised p-3 font-mono text-[11.5px]">{JSON.stringify(e.details, null, 2)}</pre>
            </div>
          )}
          <div className="rounded-lg border border-line p-3 font-mono text-[11.5px] leading-relaxed text-muted">
            <div>prev_hash  {e.prev_hash}</div>
            <div>entry_hash {e.entry_hash}</div>
            <div>hmac       {e.hmac ?? "— (legacy entry)"} {e.hmac_kid && <span className="text-faint">({e.hmac_kid})</span>}</div>
          </div>
        </div>
      )}
    </Dialog>
  );
}

export default function Audit() {
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState({ agent_id: "", outcome: "", action_type: "" });
  const [pages, setPages] = useState([]);
  const [verify, setVerify] = useState(null);
  const [verifying, setVerifying] = useState(false);
  const can = useCan();
  const toast = useToast();
  const qs = new URLSearchParams(Object.entries({ ...filters, limit: "50" }).filter(([, v]) => v));
  const first = useQuery({ queryKey: ["audit", qs.toString()], queryFn: () => api(`/audit-log?${qs}`), refetchInterval: 5000 });
  const pipeline = useQuery({ queryKey: ["pipeline"], queryFn: () => api("/audit/pipeline"), enabled: can("audit:verify"), refetchInterval: 10000 });
  const anchors = useQuery({ queryKey: ["anchors"], queryFn: () => api("/audit/anchors?limit=5"), enabled: can("audit:verify") });
  useEffect(() => setPages([]), [filters]);

  const rows = [...(first.data ?? []), ...pages.flat()];
  async function more() {
    const before = rows.at(-1)?.seq;
    const next = await api(`/audit-log?${qs}&before_seq=${before}`);
    setPages((p) => [...p, next]);
  }
  async function runVerify() {
    setVerifying(true);
    try { setVerify(await api("/audit-log/verify")); } catch (e) { toast(e.message, "bad"); } finally { setVerifying(false); }
  }

  return (
    <div className="space-y-6">
      <EntryDialog id={params.get("entry")} onClose={() => setParams({})} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Audit ledger</h1>
          <p className="text-sm text-muted">Append-only. Each entry is hash-chained to the one before it and HMAC-signed; the chain head is signed periodically.</p>
        </div>
        <div className="flex gap-2">
          {can("audit:verify") && <Button onClick={runVerify} busy={verifying}>Verify whole ledger</Button>}
          {can("audit:export") && <Button onClick={() => download("/audit/export", "veyrqun-audit.ndjson").catch((e) => toast(e.message, "bad"))}>Signed export</Button>}
        </div>
      </div>

      {verify && (
        <div className={`card animate-in px-5 py-4 ${verify.valid ? "border-ok" : "border-bad"}`} role="status">
          {verify.valid
            ? <span className="text-ok">✓ Intact: {verify.entries_checked} entries re-hashed, every link and signature checked, {verify.anchors_checked} signed anchors matched.</span>
            : <span className="text-bad">✗ Broken at entry #{verify.seq}: {verify.reason}.</span>}
        </div>
      )}

      {can("audit:verify") && pipeline.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 text-sm">
          <div className="card px-4 py-3"><div className="text-xs text-muted">Waiting to be written</div><div className="text-lg font-semibold tabular-nums">{pipeline.data.backlog ?? 0}</div></div>
          <div className="card px-4 py-3"><div className="text-xs text-muted">In flight</div><div className="text-lg font-semibold tabular-nums">{pipeline.data.pending ?? 0}</div></div>
          <div className="card px-4 py-3"><div className="text-xs text-muted">Dead letters</div><div className={`text-lg font-semibold tabular-nums ${pipeline.data.dead_letters ? "text-bad" : ""}`}>{pipeline.data.dead_letters}</div></div>
          <div className="card px-4 py-3"><div className="text-xs text-muted">Latest signed anchor</div><div className="text-lg font-semibold tabular-nums">{anchors.data?.[0] ? `#${anchors.data[0].seq}` : "—"}</div></div>
        </div>
      )}

      <Card pad={false}>
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Input placeholder="Agent id" className="w-40" value={filters.agent_id} onChange={(e) => setFilters({ ...filters, agent_id: e.target.value })} />
          <Input placeholder="Action" className="w-40" value={filters.action_type} onChange={(e) => setFilters({ ...filters, action_type: e.target.value })} />
          <Select className="w-44" value={filters.outcome} onChange={(e) => setFilters({ ...filters, outcome: e.target.value })}>
            <option value="">Any outcome</option><option value="allow">Allowed</option><option value="redact">Redacted</option>
            <option value="block">Blocked</option><option value="require_approval">Needs approval</option><option value="executed">Operator actions</option>
          </Select>
        </div>
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="border-b border-line text-left text-xs text-muted">
                <tr><th className="px-4 py-2 font-medium">#</th><th className="px-4 py-2 font-medium">Time</th><th className="px-4 py-2 font-medium">Agent</th>
                  <th className="px-4 py-2 font-medium">Action</th><th className="px-4 py-2 font-medium text-right">Amount</th><th className="px-4 py-2 font-medium">Outcome</th><th className="px-4 py-2 font-medium">Why</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((e) => (
                  <tr key={e.id} className="cursor-pointer hover:bg-raised" onClick={() => setParams({ entry: e.id })}>
                    <td className="px-4 py-2 font-mono text-xs text-faint">{e.seq}</td>
                    <td className="px-4 py-2 text-xs text-muted whitespace-nowrap">{time(e.ts)}</td>
                    <td className="px-4 py-2">{e.agent_id}</td>
                    <td className="px-4 py-2 font-mono text-xs">{e.action_type}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{Number(e.amount) ? money(e.amount, e.currency) : ""}</td>
                    <td className="px-4 py-2"><OutcomePill outcome={e.outcome} /></td>
                    <td className="max-w-xs truncate px-4 py-2 text-xs text-muted">{e.reason ?? `by ${e.actor}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="border-t border-line p-3 text-center"><Button size="sm" variant="ghost" onClick={more}>Load older entries</Button></div>
          </div>
        ) : <Empty title="No entries match" />}
      </Card>
    </div>
  );
}
