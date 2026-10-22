import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { Button, Dialog, Empty, Field, Input, Meter, Pill, useToast } from "../components/ui";
import { api } from "../lib/api";
import { Can } from "../lib/auth";
import { money, spendRatio } from "../lib/format";

const STATUS_TONE = { active: "ok", stopped: "wait", revoked: "bad" };

function NewAgent({ open, onClose }) {
  const [f, setF] = useState({ agent_id: "", display_name: "", allowed: "llm.chat, account_summary", cap: "100.00", per_tx: "50.00" });
  const [creds, setCreds] = useState(null);
  const toast = useToast();
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () => api("/agents", { method: "POST", body: {
      agent_id: f.agent_id.trim(), display_name: f.display_name.trim(),
      allowed_actions: f.allowed.split(",").map((s) => s.trim()).filter(Boolean),
      spend_cap_daily: f.cap, max_per_transaction: f.per_tx || null,
    } }),
    onSuccess: (r) => { setCreds(r.credentials); qc.invalidateQueries({ queryKey: ["agents"] }); },
    onError: (e) => toast(e.message, "bad"),
  });
  const close = () => { setCreds(null); onClose(); };
  return (
    <Dialog open={open} onClose={close} title={creds ? "Agent registered" : "Register an agent"}
      footer={creds ? <Button variant="primary" onClick={close}>I've stored the secret</Button> : (
        <><Button variant="ghost" onClick={close}>Cancel</Button><Button variant="primary" busy={create.isPending} onClick={() => create.mutate()}>Register</Button></>)}>
      {creds ? (
        <div className="space-y-3 text-sm">
          <p className="text-muted">This secret is shown once. The agent exchanges it for 15-minute tokens at <code>/oauth/token</code>.</p>
          <Field label="client_id"><Input readOnly value={creds.client_id} /></Field>
          <Field label="client_secret"><Input readOnly value={creds.client_secret} onFocus={(e) => e.target.select()} /></Field>
        </div>
      ) : (
        <div className="space-y-3">
          <Field label="Agent id" hint="lowercase, digits, _ or -"><Input value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })} /></Field>
          <Field label="Display name"><Input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
          <Field label="Allowed actions" hint="comma separated; everything else is denied"><Input value={f.allowed} onChange={(e) => setF({ ...f, allowed: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Daily cap (USD)"><Input value={f.cap} onChange={(e) => setF({ ...f, cap: e.target.value })} /></Field>
            <Field label="Per transaction"><Input value={f.per_tx} onChange={(e) => setF({ ...f, per_tx: e.target.value })} /></Field>
          </div>
        </div>
      )}
    </Dialog>
  );
}

export default function Agents() {
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api("/agents"), refetchInterval: 5000 });
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Agents</h1>
          <p className="text-sm text-muted">Each agent has an identity, an allowlist, money limits and an accountable owner.</p>
        </div>
        <Can perm="agents:register"><Button variant="primary" onClick={() => setOpen(true)}>Register agent</Button></Can>
      </div>
      <NewAgent open={open} onClose={() => setOpen(false)} />
      {agents.data?.length ? (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="border-b border-line text-left text-xs text-muted">
              <tr><th className="px-4 py-2 font-medium">Agent</th><th className="px-4 py-2 font-medium">Status</th><th className="px-4 py-2 font-medium">Owner</th>
                <th className="px-4 py-2 font-medium">Spend today</th><th className="px-4 py-2 font-medium">Limits</th><th className="px-4 py-2 font-medium">Policy</th></tr>
            </thead>
            <tbody className="divide-y divide-line">
              {agents.data.map((a) => (
                <tr key={a.agent_id} className="hover:bg-raised">
                  <td className="px-4 py-3"><Link to={`/agents/${a.agent_id}`} className="font-medium hover:text-brand">{a.display_name}</Link><div className="font-mono text-xs text-faint">{a.agent_id}</div></td>
                  <td className="px-4 py-3"><Pill tone={STATUS_TONE[a.status]}>{a.status}</Pill>{a.mode === "observe" && <Pill className="ml-1">observe</Pill>}</td>
                  <td className="px-4 py-3 text-muted">{a.accountable_owner ?? "—"}</td>
                  <td className="w-48 px-4 py-3"><div className="mb-1 text-xs tabular-nums">{a.spend_used_today === null ? "unavailable" : `${money(a.spend_used_today)} / ${money(a.spend_cap_daily)}`}</div><Meter ratio={spendRatio(a)} label="spend" /></td>
                  <td className="px-4 py-3 text-xs text-muted">≤ {money(a.max_per_transaction)} each{a.approval_threshold ? ` · approval > ${money(a.approval_threshold)}` : ""}</td>
                  <td className="px-4 py-3 text-xs">v{a.policy_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="card"><Empty title="No agents yet" /></div>}
    </div>
  );
}
