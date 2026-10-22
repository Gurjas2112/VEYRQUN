import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { api } from "../lib/api";
import { useMe } from "../lib/auth";
import { until } from "../lib/format";
import CommandPalette from "./CommandPalette";
import { Button, Pill } from "./ui";

const NAV = [
  { to: "/", label: "Today", icon: "◎", end: true },
  { to: "/playground", label: "Playground", icon: "▷" },
  { to: "/approvals", label: "Approvals", icon: "✓", badge: "approvals" },
  { to: "/agents", label: "Agents", icon: "◇" },
  { to: "/audit", label: "Audit ledger", icon: "≡" },
  { to: "/system", label: "System", icon: "⚙" },
];

export function Logo({ className = "" }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight ${className}`}>
      <svg viewBox="0 0 64 64" className="size-7" aria-hidden>
        <rect width="64" height="64" rx="14" fill="var(--brand)" />
        <path d="M16 20h10l6 16 6-16h10L36 48h-8Z" fill="var(--on-brand)" />
        <circle cx="48" cy="46" r="5" fill="#7ee0b0" />
      </svg>
      VEYRQUN
    </span>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? "light");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("vq-theme", theme); } catch { /* ignore */ }
  }, [theme]);
  return (
    <Button variant="ghost" size="sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
      {theme === "dark" ? "☀ Light" : "☾ Dark"}
    </Button>
  );
}

export default function Shell() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const pending = useQuery({
    queryKey: ["approvals", "pending"], queryFn: () => api("/approvals?status=pending"),
    refetchInterval: 5000, enabled: (me?.permissions ?? []).includes("approvals:read"),
  });
  const fleet = useQuery({ queryKey: ["fleet"], queryFn: () => api("/fleet/status"), refetchInterval: 5000 });
  const badges = useMemo(() => ({ approvals: pending.data?.length || 0 }), [pending.data]);
  const workspace = me?.memberships?.find((m) => m.workspace_id === me.workspace_id);

  async function signOut() {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* the session may already be gone */ }
    qc.clear();
    navigate("/login");
  }

  return (
    <div className="flex min-h-full">
      <aside className={`fixed inset-y-0 left-0 z-30 w-60 shrink-0 border-r border-line bg-surface px-3 py-4 transition-transform md:static md:translate-x-0 ${menuOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="px-2"><Logo /></div>
        <div className="mt-1 px-2 text-xs text-faint">Agent operations</div>
        <nav className="mt-6 flex flex-col gap-0.5" onClick={() => setMenuOpen(false)}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => `flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm ${isActive ? "bg-brand-soft text-brand font-medium" : "text-muted hover:bg-raised hover:text-ink"}`}>
              <span className="w-4 text-center" aria-hidden>{n.icon}</span>
              <span className="flex-1">{n.label}</span>
              {n.badge && badges[n.badge] > 0 && <Pill tone="wait">{badges[n.badge]}</Pill>}
            </NavLink>
          ))}
        </nav>
        <div className="absolute inset-x-3 bottom-4 rounded-lg border border-line bg-raised p-3 text-xs">
          <div className="font-medium">{me?.demo?.guest ? "Demo guest" : me?.email ?? "Signed in"}</div>
          <div className="mt-0.5 text-faint">{workspace?.slug ?? "workspace"} · {(me?.roles ?? []).map((r) => r.replace("_", " ")).join(", ")}</div>
          <div className="mt-2 flex items-center justify-between">
            <ThemeToggle />
            <Button variant="ghost" size="sm" onClick={signOut}>Sign out</Button>
          </div>
        </div>
      </aside>
      {menuOpen && <div className="fixed inset-0 z-20 bg-black/30 md:hidden" onClick={() => setMenuOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        {me?.demo?.guest && (
          <div className="border-b border-line bg-brand-soft px-4 py-2 text-center text-xs text-brand">
            Public demo · your private sandbox with synthetic agents and money · closes in {until(me.demo.expires_at)}
          </div>
        )}
        {fleet.data?.fleet_stopped && (
          <div className="border-b border-bad bg-bad-soft px-4 py-2 text-center text-sm font-medium text-bad" role="alert">
            Fleet kill switch is ON. Every agent request is refused until it is released.
          </div>
        )}
        <header className="sticky top-0 z-10 flex h-14 items-center gap-3 border-b border-line bg-canvas/90 px-4 backdrop-blur md:px-8">
          <button className="md:hidden text-lg" onClick={() => setMenuOpen(true)} aria-label="Open menu">☰</button>
          <CommandPalette />
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
