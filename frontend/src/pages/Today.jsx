import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { Feed } from "../components/Decisions";
import { Card, Empty, Meter, Pill, Stat } from "../components/ui";
import { api } from "../lib/api";
import { useCan, useMe } from "../lib/auth";
import { money, spendRatio, until } from "../lib/format";

const REASON = {
  action_not_permitted: "Forbidden action", action_not_in_allowlist: "Not on allowlist",
  transaction_limit_exceeded: "Over per-transaction limit", spend_cap_exceeded: "Daily cap reached",
  action_spend_cap_exceeded: "Action cap reached", rate_limit_exceeded: "Rate limited",
  injection_suspected: "Prompt injection", data_class_not_permitted: "Sensitive data",
  emergency_stop_active: "Agent stopped", fleet_emergency_stop_active: "Fleet stopped", agent_revoked: "Agent revoked",
  policy_undefined: "No matching policy", unknown_agent: "Unknown agent", state_store_unavailable: "Counters unreachable",
};

export default function Today() {
  const { data: me } = useMe();
  const can = useCan();
  const navigate = useNavigate();
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api("/stats?hours=24"), refetchInterval: 5000 });
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api("/agents"), refetchInterval: 5000 });
  const approvals = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => api("/approvals?status=pending"),
    refetchInterval: 5000, enabled: can("approvals:read") });

  const s = stats.data;
  const attention = [
    ...(approvals.data ?? []).map((a) => ({ key: a.approval_id, tone: "wait", title: `${a.agent_id ?? "Change"} wants ${a.action_type ?? a.kind}${a.amount ? ` · ${money(a.amount, a.currency)}` : ""}`,
      hint: `Approval expires in ${until(a.expires_at)}`, to: "/approvals" })),
    ...(agents.data ?? []).filter((a) => a.status !== "active").map((a) => ({ key: a.agent_id, tone: "bad",
      title: `${a.display_name} is ${a.status}`, hint: a.status_reason ?? "", to: `/agents/${a.agent_id}` })),
    ...(agents.data ?? []).filter((a) => a.status === "active" && spendRatio(a) >= 0.8).map((a) => ({ key: `${a.agent_id}-cap`, tone: "wait",
      title: `${a.display_name} has used ${Math.round(spendRatio(a) * 100)}% of today's cap`, hint: `${money(a.spend_used_today)} of ${money(a.spend_cap_daily)}`, to: `/agents/${a.agent_id}` })),
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Today</h1>
        <p className="text-sm text-muted">
          {me?.demo?.guest ? "Your sandbox. Everything here is synthetic and private to you." : "What your agents did, and what needs you."}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Decisions (24h)" value={s?.decisions ?? "—"} />
        <Stat label="Blocked" value={s ? (s.by_outcome.block ?? 0) : "—"} tone="bad"
          hint={s?.block_rate != null ? `${Math.round(s.block_rate * 100)}% of decisions` : "no traffic yet"} />
        <Stat label="Waiting for approval" value={approvals.data?.length ?? "—"} tone={approvals.data?.length ? "wait" : undefined} />
        <Stat label="Masked before leaving" value={s ? (s.by_outcome.redact ?? 0) : "—"} tone="mask" />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-6">
          <Card title="Needs your attention" pad={false}>
            {attention.length ? (
              <ul className="divide-y divide-line">
                {attention.map((a) => (
                  <li key={a.key}>
                    <Link to={a.to} className="flex items-center gap-3 px-4 py-3 hover:bg-raised">
                      <span className={`size-2 rounded-full ${a.tone === "bad" ? "bg-bad" : "bg-wait"}`} aria-hidden />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">{a.title}</div>
                        <div className="text-xs text-faint">{a.hint}</div>
                      </div>
                      <span className="text-muted">→</span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : <Empty title="All clear">No pending approvals, no stopped agents, nobody near a spend cap.</Empty>}
          </Card>
          <Card title="Live decisions" action={<Link to="/audit" className="text-xs text-brand">Full ledger →</Link>} pad={false}>
            <Feed limit={12} onSelect={(e) => navigate(`/audit?entry=${e.id}`)} />
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Agents" action={<Link to="/agents" className="text-xs text-brand">All →</Link>} pad={false}>
            <ul className="divide-y divide-line">
              {(agents.data ?? []).map((a) => (
                <li key={a.agent_id}>
                  <Link to={`/agents/${a.agent_id}`} className="block px-4 py-3 hover:bg-raised">
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium">{a.display_name}</span>
                      {a.status !== "active" ? <Pill tone="bad">{a.status}</Pill> : <span className="text-xs text-faint tabular-nums">{money(a.spend_used_today)} / {money(a.spend_cap_daily)}</span>}
                    </div>
                    <div className="mt-2"><Meter ratio={spendRatio(a)} label={`${a.display_name} spend`} /></div>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Why things were blocked">
            {s && Object.keys(s.by_rule).length ? (
              <ul className="space-y-2 text-sm">
                {Object.entries(s.by_rule).sort((a, b) => b[1] - a[1]).map(([rule, n]) => (
                  <li key={rule} className="flex items-center justify-between gap-2">
                    <span>{REASON[rule] ?? rule}</span><span className="tabular-nums text-muted">{n}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted">Nothing blocked in the last 24 hours.</p>}
          </Card>
        </div>
      </div>
    </div>
  );
}
