import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Feed } from "../components/Decisions";
import { Button, Card, Code, Dialog, Empty, Field, Input, KeyValue, Meter, Pill, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { ago, money, spendRatio } from "../lib/format";

const list = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
const toForm = (a) => ({
  allowed_actions: a.allowed_actions.join(", "), blocked_actions: a.blocked_actions.join(", "),
  spend_cap_daily: a.spend_cap_daily ?? "", max_per_transaction: a.max_per_transaction ?? "",
  approval_threshold: a.approval_threshold ?? "", rate_limit_per_minute: a.rate_limit_per_minute ?? "",
  redact_data_classes: a.redact_data_classes.join(", "), block_data_classes: a.block_data_classes.join(", "), note: "",
});

function PolicyEditor({ agent }) {
  const [f, setF] = useState(() => toForm(agent));
  const toast = useToast();
  const qc = useQueryClient();
  useEffect(() => setF(toForm(agent)), [agent]);
  const save = useMutation({
    mutationFn: () => api(`/agents/${agent.agent_id}/policy`, { method: "PATCH", body: {
      allowed_actions: list(f.allowed_actions), blocked_actions: list(f.blocked_actions),
      spend_cap_daily: f.spend_cap_daily, max_per_transaction: f.max_per_transaction || null,
      approval_threshold: f.approval_threshold || null,
      rate_limit_per_minute: f.rate_limit_per_minute ? Number(f.rate_limit_per_minute) : null,
      redact_data_classes: list(f.redact_data_classes), block_data_classes: list(f.block_data_classes),
      note: f.note || null,
    } }),
    onSuccess: (r) => {
      toast(r.approval ? "Change request created: an approver must publish it" : `Published policy v${r.policy_version}`, "ok");
      qc.invalidateQueries();
    },
    onError: (e) => toast(e.message, "bad"),
  });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="grid gap-4 md:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Field label="Allowed actions" hint="Everything not listed is denied"><Input value={f.allowed_actions} onChange={set("allowed_actions")} /></Field>
      <Field label="Always blocked"><Input value={f.blocked_actions} onChange={set("blocked_actions")} /></Field>
      <Field label="Daily spend cap (USD)"><Input value={f.spend_cap_daily} onChange={set("spend_cap_daily")} required /></Field>
      <Field label="Per-transaction limit"><Input value={f.max_per_transaction} onChange={set("max_per_transaction")} placeholder="no limit" /></Field>
      <Field label="Human approval above"><Input value={f.approval_threshold} onChange={set("approval_threshold")} placeholder="never" /></Field>
      <Field label="Requests per minute"><Input value={f.rate_limit_per_minute} onChange={set("rate_limit_per_minute")} placeholder="unlimited" /></Field>
      <Field label="Mask these data types" hint="email, phone, pci_pan, iban, aadhaar, secret"><Input value={f.redact_data_classes} onChange={set("redact_data_classes")} /></Field>
      <Field label="Refuse requests containing"><Input value={f.block_data_classes} onChange={set("block_data_classes")} /></Field>
      <div className="md:col-span-2"><Field label="Change note (kept in history)"><Input value={f.note} onChange={set("note")} maxLength={280} /></Field></div>
      <div className="md:col-span-2 flex items-center gap-3">
        <Button variant="primary" busy={save.isPending}>Publish change</Button>
        <span className="text-xs text-faint">Validated server-side: caps ≥ 0, per-transaction ≤ daily cap, an action can't be both allowed and blocked.</span>
      </div>
    </form>
  );
}

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const [confirm, dialog] = useConfirm();
  const [secret, setSecret] = useState(null);
  const agent = useQuery({ queryKey: ["agent", agentId], queryFn: () => api(`/agents/${agentId}`), refetchInterval: 5000 });
  const history = useQuery({ queryKey: ["history", agentId], queryFn: () => api(`/agents/${agentId}/policy-history`) });

  const op = useMutation({
    mutationFn: ({ path, body }) => api(path, { method: "POST", body }),
    onSuccess: (r) => { toast(r?.message ?? "Done", "ok"); qc.invalidateQueries(); },
    onError: (e) => toast(e.message, "bad"),
  });

  async function control(kind) {
    const map = {
      stop: { path: `/emergency-stop/${agentId}`, title: "Stop this agent now?", body: "Every request it makes is refused within about a second, everywhere.", danger: true, askReason: true },
      resume: { path: `/resume/${agentId}`, title: "Resume this agent?", body: "It goes back to normal policy checks." },
      revoke: { path: `/agents/${agentId}/revoke`, title: "Revoke this agent?", body: "Revocation is for compromised agents. It stays revoked until reinstated.", danger: true, askReason: true },
      reinstate: { path: `/agents/${agentId}/reinstate`, title: "Reinstate this agent?", body: "It returns to active." },
      reset: { path: `/agents/${agentId}/reset-spend`, title: "Reset today's spend?", body: "The counter goes back to zero (recorded in the ledger)." },
    }[kind];
    const res = await confirm({ ...map, confirmLabel: "Confirm" });
    if (res) op.mutate({ path: map.path, body: map.askReason ? { reason: res.reason } : undefined });
  }

  async function rotate() {
    const res = await confirm({ title: "Rotate the client secret?", body: "The old secret stops working for new tokens immediately.", confirmLabel: "Rotate" });
    if (!res) return;
    try { setSecret(await api(`/agents/${agentId}/rotate-secret`, { method: "POST" })); } catch (e) { toast(e.message, "bad"); }
  }

  async function rollback(v) {
    const res = await confirm({ title: `Roll back to v${v}?`, body: "This publishes the old limits as a new version; history is never rewritten.", confirmLabel: "Roll back" });
    if (res) op.mutate({ path: `/agents/${agentId}/policy/rollback/${v}` });
  }

  if (agent.error?.status === 404) return <Empty title="Agent not found"><Link className="text-brand underline" to="/agents">Back to agents</Link></Empty>;
  const a = agent.data;
  if (!a) return null;

  return (
    <div className="space-y-6">
      {dialog}
      <Dialog open={!!secret} onClose={() => setSecret(null)} title="New client secret" footer={<Button variant="primary" onClick={() => setSecret(null)}>Done</Button>}>
        <p className="mb-3 text-sm text-muted">Shown once. Update the agent's secret store now.</p>
        <Field label="client_secret"><Input readOnly value={secret?.client_secret ?? ""} onFocus={(e) => e.target.select()} /></Field>
      </Dialog>
      <button className="text-sm text-muted hover:text-ink" onClick={() => navigate("/agents")}>← Agents</button>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{a.display_name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
            <Code>{a.agent_id}</Code><Pill tone={a.status === "active" ? "ok" : a.status === "stopped" ? "wait" : "bad"}>{a.status}</Pill>
            <span>owned by {a.accountable_owner ?? "—"} · {a.business_service ?? "—"}</span>
          </div>
          {a.status_reason && <p className="mt-1 text-sm text-muted">Reason: {a.status_reason}</p>}
        </div>
        {can("agents:control") && (
          <div className="flex flex-wrap gap-2">
            {a.status === "active" && <Button variant="danger" onClick={() => control("stop")}>Stop agent</Button>}
            {a.status === "stopped" && <Button variant="primary" onClick={() => control("resume")}>Resume</Button>}
            {a.status !== "revoked" ? <Button variant="quiet_danger" onClick={() => control("revoke")}>Revoke</Button>
              : <Button onClick={() => control("reinstate")}>Reinstate</Button>}
            {can("spend:reset") && <Button onClick={() => control("reset")}>Reset spend</Button>}
            {can("agents:rotate_secret") && <Button onClick={rotate}>Rotate secret</Button>}
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          {can("policy:edit") ? <Card title={`Policy · v${a.policy_version}`}><PolicyEditor agent={a} /></Card> : (
            <Card title={`Policy · v${a.policy_version}`}><KeyValue items={[["Allowed", a.allowed_actions.join(", ")], ["Blocked", a.blocked_actions.join(", ") || "—"]]} /></Card>
          )}
          <Card title="Recent decisions" pad={false}><Feed agentId={agentId} limit={10} onSelect={(e) => navigate(`/audit?entry=${e.id}`)} /></Card>
        </div>
        <div className="space-y-6">
          <Card title="Spend today">
            <div className="text-2xl font-semibold tabular-nums">{money(a.spend_used_today)}</div>
            <div className="mb-2 text-xs text-faint">of {money(a.spend_cap_daily)} ({a.timezone})</div>
            <Meter ratio={spendRatio(a)} label="spend" />
          </Card>
          <Card title="Connect this agent">
            <p className="text-xs text-muted">Agents never call models or bank APIs directly. Point any OpenAI- or MCP-compatible client at the gateway:</p>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-raised p-3 font-mono text-[11.5px] leading-relaxed">{`TOKEN=$(curl -s ${location.origin}/api/oauth/token \\
  -d '{"grant_type":"client_credentials",
       "client_id":"<id>","client_secret":"<secret>"}' \\
  -H 'content-type: application/json' | jq -r .access_token)

curl ${location.origin}/mcp -H "authorization: Bearer $TOKEN" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"${a.allowed_actions.find((x) => x !== "llm.chat") ?? "account_summary"}","arguments":{}}}'`}</pre>
          </Card>
          <Card title="Policy history" pad={false}>
            <ul className="divide-y divide-line">
              {(history.data ?? []).map((h) => (
                <li key={h.version} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                  <span className="font-mono text-xs">v{h.version}</span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{h.note ?? "update"}</div>
                    <div className="text-xs text-faint">{h.created_by} · {ago(h.created_at)}</div>
                  </div>
                  {h.version !== a.policy_version && can("policy:edit") && <Button size="sm" variant="ghost" onClick={() => rollback(h.version)}>Roll back</Button>}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
