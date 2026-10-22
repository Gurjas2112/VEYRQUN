import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../lib/api";
import { ago, money, titleCase } from "../lib/format";
import { Code, Empty, KeyValue, OutcomePill, Pill } from "./ui";

export function useFeed({ limit = 20, agentId } = {}) {
  const qs = new URLSearchParams({ limit: String(limit), ...(agentId ? { agent_id: agentId } : {}) });
  return useQuery({ queryKey: ["feed", limit, agentId], queryFn: () => api(`/audit-log?${qs}`), refetchInterval: 3000 });
}

const isOperator = (e) => e.action_type.startsWith("operator:") || e.action_type.startsWith("auth:");

export function FeedRow({ e, onClick }) {
  const op = isOperator(e);
  return (
    <li>
      <button onClick={onClick} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-raised">
        <div className="w-36 shrink-0 pt-0.5">{op ? <Pill>Operator</Pill> : <OutcomePill outcome={e.outcome} />}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
            <span className="font-medium">{e.agent_id === "*fleet*" ? "Whole fleet" : e.agent_id}</span>
            <span className="text-muted">{op ? titleCase(e.action_type.split(":")[1]) : e.action_type}</span>
            {!op && Number(e.amount) > 0 && <span className="tabular-nums">{money(e.amount, e.currency)}</span>}
          </div>
          <div className="truncate text-xs text-faint">
            {op ? `by ${e.actor}` : e.reason}
          </div>
        </div>
        <div className="shrink-0 text-xs text-faint">{ago(e.ts)}</div>
      </button>
    </li>
  );
}

export function Feed({ limit = 15, agentId, onSelect }) {
  const feed = useFeed({ limit, agentId });
  if (feed.isLoading) return <Empty title="Loading decisions…" />;
  if (!feed.data?.length) return <Empty title="No decisions yet">Run something in the Playground and it shows up here within seconds.</Empty>;
  return (
    <ul className="divide-y divide-line">
      {feed.data.map((e) => <FeedRow key={e.id} e={e} onClick={() => onSelect?.(e)} />)}
    </ul>
  );
}

/** The answer to "what happened and why", for one gateway decision. */
export function DecisionCard({ result }) {
  const d = result.detail ?? {};
  const signals = d.signals ?? {};
  const resp = result.response ?? {};
  const modelText = resp?.choices?.[0]?.message?.content;
  const toolText = resp?.result?.content?.map((c) => c.text).join(" ");
  return (
    <div className="card animate-in overflow-hidden">
      <div className="border-b border-line px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <OutcomePill outcome={result.outcome} large />
          {result.rule_id && <Code>{result.rule_id}</Code>}
          <span className="ml-auto text-xs text-faint">HTTP {result.http_status}</span>
        </div>
        <p className="mt-3 text-[15px] leading-snug">{result.reason}</p>
      </div>
      <div className="grid gap-5 px-5 py-4 md:grid-cols-2">
        <KeyValue items={[
          ["Action", d.action ?? result.request?.params?.name ?? "llm.chat"],
          ["Amount checked", d.amount_minor !== undefined ? money(d.amount_minor / 100, d.currency) : "—"],
          ["Spend today", `${money(result.spend_before)} → ${money(result.spend_after)}`],
          ["Policy", d.policy_version ? `v${d.policy_version} · ${d.mode}` : "—"],
        ]} />
        <KeyValue items={[
          ["Data found", d.data_classes?.length ? d.data_classes.map((c) => <Pill key={c} tone="mask" className="mr-1">{c}</Pill>) : "none"],
          ["Masked on the way", d.request_redactions?.length ? d.request_redactions.join(", ") : "nothing"],
          ["Injection signal", signals.injection_score != null
            ? <span key="s">{signals.injection_score}{signals.injection_patterns?.length ? ` · ${signals.injection_patterns.join(", ")}` : ""}</span> : "—"],
          ["Decision id", result.decision_id ? <Code key="d">{result.decision_id.slice(0, 18)}…</Code> : "—"],
        ]} />
      </div>
      {(modelText || toolText) && (
        <div className="border-t border-line px-5 py-4">
          <div className="text-xs font-medium text-muted">{modelText ? "What the model returned (after the output scan)" : "What the tool returned"}</div>
          <p className="mt-1 whitespace-pre-wrap rounded-lg bg-raised p-3 font-mono text-[12.5px]">{modelText ?? toolText}</p>
        </div>
      )}
      {result.audit_entry && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line bg-raised px-5 py-3 text-xs">
          <Pill tone="ok">✓ in the signed ledger</Pill>
          <span className="text-muted">entry #{result.audit_entry.seq}</span>
          <span className="font-mono text-faint">hash {result.audit_entry.entry_hash.slice(0, 16)}…</span>
          <Link to={`/audit?entry=${result.audit_entry.id}`} className="ml-auto text-brand underline">Open in ledger</Link>
        </div>
      )}
    </div>
  );
}
