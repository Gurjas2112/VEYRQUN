import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { outcomeMeta } from "../lib/format";

const cx = (...c) => c.filter(Boolean).join(" ");

const TONES = {
  ok: "bg-ok-soft text-ok",
  bad: "bg-bad-soft text-bad",
  wait: "bg-wait-soft text-wait",
  mask: "bg-mask-soft text-mask",
  brand: "bg-brand-soft text-brand",
  neutral: "bg-raised text-muted border border-line",
};

export function Pill({ tone = "neutral", children, className }) {
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function OutcomePill({ outcome, large }) {
  const m = outcomeMeta(outcome);
  return (
    <Pill tone={m.tone} className={large ? "text-sm px-3 py-1" : undefined}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {m.label}
    </Pill>
  );
}

const BUTTONS = {
  primary: "bg-brand text-on-brand hover:opacity-90",
  secondary: "bg-surface text-ink border border-line hover:border-line-strong",
  ghost: "text-muted hover:text-ink hover:bg-raised",
  danger: "bg-bad text-white hover:opacity-90",
  quiet_danger: "text-bad border border-line hover:border-bad bg-surface",
};

export function Button({ variant = "secondary", size = "md", className, busy, children, ...props }) {
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        BUTTONS[variant], className,
      )}
      disabled={busy || props.disabled}
      {...props}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

export const Spinner = () => (
  <span className="inline-block size-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" aria-label="Loading" />
);

export function Card({ title, action, children, className, pad = true }) {
  return (
    <section className={cx("card", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className={pad ? "p-4" : undefined}>{children}</div>
    </section>
  );
}

const STAT_TONES = { ok: "text-ok", bad: "text-bad", wait: "text-wait", mask: "text-mask" };

export function Stat({ label, value, hint, tone }) {
  return (
    <div className="card px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums", STAT_TONES[tone])}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-faint">{hint}</div>}
    </div>
  );
}

export function Meter({ ratio, label }) {
  const pct = Math.round((ratio ?? 0) * 100);
  const tone = pct >= 90 ? "bg-bad" : pct >= 70 ? "bg-wait" : "bg-ok";
  return (
    <div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised border border-line" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={cx("h-full rounded-full transition-all", tone)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-6 py-10 text-center">
      <div className="text-sm font-medium">{title}</div>
      {children && <div className="max-w-sm text-xs text-muted">{children}</div>}
    </div>
  );
}

export function Code({ children, className }) {
  return <code className={cx("rounded bg-raised border border-line px-1.5 py-0.5 font-mono text-[12px]", className)}>{children}</code>;
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

const INPUT = "h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm placeholder:text-faint focus:border-brand focus:outline-none";
export const Input = (props) => <input {...props} className={cx(INPUT, props.className)} />;
export const Select = (props) => <select {...props} className={cx(INPUT, "pr-8", props.className)} />;
export const Textarea = (props) => (
  <textarea {...props} className={cx(INPUT, "h-auto min-h-20 py-2 leading-relaxed", props.className)} />
);

/** Native <dialog>: focus trap, Esc to close and backdrop come from the browser. */
export function Dialog({ open, onClose, title, children, footer, wide }) {
  const ref = useRef(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} className={cx("card m-auto w-[calc(100%-2rem)] p-0", wide ? "max-w-2xl" : "max-w-md")}>
      {open && (
        <div className="animate-in">
          <header className="flex items-center justify-between border-b border-line px-5 py-3">
            <h2 className="text-base font-semibold">{title}</h2>
            <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Close">✕</button>
          </header>
          <div className="px-5 py-4">{children}</div>
          {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
        </div>
      )}
    </dialog>
  );
}

/** Ask for a decision (and optionally a reason), never window.prompt. */
export function useConfirm() {
  const [state, setState] = useState(null);
  const [reason, setReason] = useState("");
  const confirm = useCallback((opts) => new Promise((resolve) => { setReason(""); setState({ ...opts, resolve }); }), []);
  const close = (value) => { state?.resolve(value); setState(null); };
  const typed = state?.typeToConfirm;
  const ok = !typed || reason.trim().toUpperCase() === typed;
  const dialog = (
    <Dialog
      open={!!state}
      onClose={() => close(null)}
      title={state?.title}
      footer={
        <>
          <Button variant="ghost" onClick={() => close(null)}>Cancel</Button>
          <Button variant={state?.danger ? "danger" : "primary"} disabled={!ok} onClick={() => close({ reason: typed ? null : reason.trim() || null })}>
            {state?.confirmLabel ?? "Confirm"}
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted">{state?.body}</p>
      {(state?.askReason || typed) && (
        <div className="mt-4">
          <Field label={typed ? `Type ${typed} to confirm` : "Reason (recorded in the audit ledger)"}>
            <Input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} maxLength={280}
              onKeyDown={(e) => { if (e.key === "Enter" && ok) close({ reason: typed ? null : reason.trim() || null }); }} />
          </Field>
        </div>
      )}
    </Dialog>
  );
  return [confirm, dialog];
}

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((message, tone = "neutral") => {
    const id = Math.random().toString(36).slice(2);
    setItems((x) => [...x, { id, message, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={cx("card animate-in px-4 py-3 text-sm", t.tone === "bad" && "border-bad", t.tone === "ok" && "border-ok")}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function KeyValue({ items }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.filter(Boolean).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
