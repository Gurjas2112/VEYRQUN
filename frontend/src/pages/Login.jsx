import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Logo } from "../components/Shell";
import { Button } from "../components/ui";
import { api, ssoLoginUrl } from "../lib/api";
import { time, until } from "../lib/format";

const POINTS = [
  ["Decided by policy", "Every model call and tool call an agent makes passes an inline gateway. A deterministic policy decides; no model ever does."],
  ["Held for a human", "Money moves over a threshold pause until an approver says yes, and the approval only works for that exact action and amount."],
  ["Signed into a ledger", "Each decision is hash-chained, HMAC-signed and anchored, so editing history afterwards is detectable."],
];

export default function Login() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const demo = useQuery({ queryKey: ["demo-status"], queryFn: () => api("/auth/demo"), retry: false });
  const next = params.get("next") || "/";
  const ended = params.get("ended") || (demo.data?.enabled && !demo.data?.active);

  async function startDemo() {
    setBusy(true);
    setError(null);
    try {
      await api("/auth/demo", { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["me"] });
      navigate("/playground");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full md:grid-cols-[1.1fr_1fr]">
      <section className="flex flex-col justify-between bg-surface px-8 py-10 md:px-14 md:py-14 border-r border-line">
        <Logo className="text-lg" />
        <div className="my-10 max-w-lg">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight md:text-4xl">
            Let AI agents act on accounts. <span className="text-brand">Keep every action accountable.</span>
          </h1>
          <ul className="mt-8 space-y-5">
            {POINTS.map(([title, body], i) => (
              <li key={title} className="flex gap-4">
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-brand-soft font-mono text-xs text-brand">{i + 1}</span>
                <div>
                  <div className="font-medium">{title}</div>
                  <div className="text-sm text-muted">{body}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-faint">Open source · MIT · started from Aegis by infyier, rebuilt as an inline gateway.</p>
      </section>

      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          {ended ? (
            <div className="card p-6">
              <h2 className="text-lg font-semibold">The public demo has ended</h2>
              <p className="mt-2 text-sm text-muted">
                The time-limited demo closed and its sandboxes were removed. The code, measured results and a
                one-command local setup are in the repository.
              </p>
              <a className="mt-4 inline-block text-sm text-brand underline" href="https://github.com/Gurjas2112/VEYRQUN">github.com/Gurjas2112/VEYRQUN</a>
            </div>
          ) : (
            <div className="card p-6">
              <h2 className="text-lg font-semibold">Sign in</h2>
              {demo.data?.active && (
                <>
                  <p className="mt-1 text-sm text-muted">
                    Try it without an account: you get a private sandbox with four agents (one of them misbehaves on purpose).
                  </p>
                  <Button variant="primary" className="mt-5 w-full" onClick={startDemo} busy={busy}>
                    Try the live demo
                  </Button>
                  <p className="mt-2 text-center text-xs text-faint">
                    Open until {time(demo.data.expires_at)} ({until(demo.data.expires_at)} left). No personal data is collected.
                  </p>
                  <div className="my-5 flex items-center gap-3 text-xs text-faint"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
                </>
              )}
              <a href={ssoLoginUrl(next)} className="flex h-9 w-full items-center justify-center rounded-lg border border-line text-sm font-medium hover:border-line-strong">
                Continue with single sign-on
              </a>
              <p className="mt-2 text-center text-xs text-faint">For workspace members invited by an admin (MFA required for operators).</p>
              {error && <p className="mt-4 rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{error}</p>}
              {params.get("no_workspace") && <p className="mt-4 text-sm text-muted">You're signed in but not a member of any workspace yet. Ask an admin for an invite.</p>}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
