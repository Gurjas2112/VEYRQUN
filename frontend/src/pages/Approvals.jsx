import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Card, Code, Empty, OutcomePill, Pill, useConfirm, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { ago, money, titleCase, until } from "../lib/format";

const TABS = [["pending", "Waiting"], ["approved", "Approved"], ["consumed", "Used"], ["rejected", "Rejected"], ["expired", "Expired"]];

function describe(a) {
  if (a.kind === "action") return `${a.agent_id} wants to run ${a.action_type}`;
  if (a.kind === "policy_publish") return `Publish a policy change for ${a.agent_id}`;
  if (a.kind === "kill_release") return "Release the fleet kill switch";
  return titleCase(a.kind);
}

export default function Approvals() {
  const [tab, setTab] = useState("pending");
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const [confirm, dialog] = useConfirm();
  const list = useQuery({ queryKey: ["approvals", tab], queryFn: () => api(`/approvals?status=${tab}`), refetchInterval: 4000 });

  const decide = useMutation({
    mutationFn: ({ id, approve, reason }) => api(`/approvals/${id}/${approve ? "approve" : "reject"}`, { method: "POST", body: { reason } }),
    onSuccess: (_, v) => { toast(v.approve ? "Approved. The agent can now retry this exact action once." : "Rejected", "ok"); qc.invalidateQueries({ queryKey: ["approvals"] }); },
    onError: (e) => toast(e.status === 401 ? "Please sign in again (recent authentication required)" : e.message, "bad"),
  });

  async function act(a, approve) {
    const res = await confirm({
      title: approve ? "Approve this action?" : "Reject this action?",
      body: `${describe(a)}${a.amount ? ` for ${money(a.amount, a.currency)}` : ""}. Your decision and reason are signed into the audit ledger.`,
      askReason: true, confirmLabel: approve ? "Approve" : "Reject", danger: !approve,
    });
    if (res) decide.mutate({ id: a.approval_id, approve, reason: res.reason });
  }

  return (
    <div className="space-y-6">
      {dialog}
      <div>
        <h1 className="text-xl font-semibold">Approvals</h1>
        <p className="text-sm text-muted">Actions above an agent's approval threshold, and sensitive changes, wait here for a second person.</p>
      </div>
      <div className="flex flex-wrap gap-1 border-b border-line">
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? "border-brand font-medium text-brand" : "border-transparent text-muted hover:text-ink"}`}>{l}</button>
        ))}
      </div>
      {list.data?.length ? (
        <div className="grid gap-4 md:grid-cols-2">
          {list.data.map((a) => (
            <Card key={a.approval_id}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{describe(a)}</div>
                  {a.amount && <div className="mt-1 text-2xl font-semibold tabular-nums">{money(a.amount, a.currency)}</div>}
                </div>
                {a.status === "pending" ? <Pill tone="wait">expires in {until(a.expires_at)}</Pill> : <OutcomePill outcome={a.status === "rejected" ? "block" : "allow"} />}
              </div>
              <div className="mt-3 space-y-1 text-xs text-muted">
                <div>Requested by <Code>{a.requested_by}</Code> · {ago(a.created_at)}</div>
                {a.decided_by && <div>Decided by <Code>{a.decided_by}</Code>{a.reason ? `: “${a.reason}”` : ""}</div>}
              </div>
              {a.status === "pending" && can("approvals:decide") && (
                <div className="mt-4 flex gap-2">
                  <Button variant="primary" onClick={() => act(a, true)} busy={decide.isPending}>Approve</Button>
                  <Button variant="quiet_danger" onClick={() => act(a, false)}>Reject</Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      ) : (
        <div className="card"><Empty title={tab === "pending" ? "Nothing waiting for you" : "Nothing here yet"}>
          Try “Needs approval” in the Playground to create one.
        </Empty></div>
      )}
    </div>
  );
}
