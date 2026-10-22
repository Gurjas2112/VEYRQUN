import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { useToast } from "./ui";

/** Ctrl/⌘+K: jump anywhere or run an operator action without hunting through pages. */
export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const can = useCan();
  const toast = useToast();
  const qc = useQueryClient();
  const input = useRef(null);
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api("/agents"), enabled: open });

  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { if (open) { setQ(""); setActive(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);

  const commands = useMemo(() => {
    const go = (label, to, hint = "Go to") => ({ label, hint, run: () => navigate(to) });
    const list = [
      go("Today", "/"), go("Playground", "/playground"), go("Approvals", "/approvals"), go("Agents", "/agents"),
      go("Audit ledger", "/audit"), go("System", "/system"),
      can("audit:verify") && {
        label: "Verify the audit ledger", hint: "Action",
        run: async () => {
          const r = await api("/audit-log/verify");
          toast(r.valid ? `Ledger intact: ${r.entries_checked} entries, ${r.anchors_checked} anchors` : `Ledger broken at #${r.seq}: ${r.reason}`, r.valid ? "ok" : "bad");
        },
      },
    ];
    for (const a of agents.data ?? []) {
      list.push(go(`Open ${a.display_name}`, `/agents/${a.agent_id}`, a.agent_id));
      if (can("agents:control") && a.status === "active") {
        list.push({
          label: `Stop ${a.display_name}`, hint: "Kill switch", danger: true,
          run: async () => {
            await api(`/emergency-stop/${a.agent_id}`, { method: "POST", body: { reason: "stopped from command palette" } });
            qc.invalidateQueries();
            toast(`${a.display_name} stopped`, "ok");
          },
        });
      }
    }
    return list.filter(Boolean);
  }, [agents.data, can, navigate, qc, toast]);

  const shown = commands.filter((c) => (c.label + " " + c.hint).toLowerCase().includes(q.toLowerCase())).slice(0, 9);

  async function run(c) {
    setOpen(false);
    try { await c.run(); } catch (e) { toast(e.message, "bad"); }
  }

  return (
    <>
      <button onClick={() => setOpen(true)}
        className="flex h-9 w-full max-w-md items-center gap-2 rounded-lg border border-line bg-surface px-3 text-left text-sm text-faint hover:border-line-strong">
        <span aria-hidden>⌕</span>
        <span className="flex-1">Search or run a command…</span>
        <kbd className="rounded border border-line px-1.5 font-mono text-[11px]">Ctrl K</kbd>
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[12vh]" onClick={() => setOpen(false)}>
          <div className="card w-full max-w-lg animate-in overflow-hidden" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Command palette">
            <input ref={input} value={q} placeholder="Type a page, an agent, or an action…"
              onChange={(e) => { setQ(e.target.value); setActive(0); }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)); }
                if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
                if (e.key === "Enter" && shown[active]) run(shown[active]);
              }}
              className="h-12 w-full border-b border-line bg-transparent px-4 text-sm focus:outline-none" />
            <ul className="max-h-80 overflow-auto p-1.5" role="listbox">
              {shown.map((c, i) => (
                <li key={c.label} role="option" aria-selected={i === active}>
                  <button onMouseEnter={() => setActive(i)} onClick={() => run(c)}
                    className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${i === active ? "bg-brand-soft" : ""} ${c.danger ? "text-bad" : ""}`}>
                    <span>{c.label}</span><span className="text-xs text-faint">{c.hint}</span>
                  </button>
                </li>
              ))}
              {!shown.length && <li className="px-3 py-6 text-center text-sm text-faint">No matches</li>}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
