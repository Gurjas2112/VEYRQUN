export function money(value, currency = "USD") {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  // Display only. Amounts are exact decimal strings from the API and are never computed with here.
  return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format(n);
}

export function ago(iso) {
  if (!iso) return "";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function until(iso) {
  if (!iso) return "";
  const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  if (s <= 0) return "expired";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

export const time = (iso) => (iso ? new Date(iso).toLocaleString() : "");

export const OUTCOMES = {
  allow: { label: "Allowed", tone: "ok" },
  redact: { label: "Allowed · redacted", tone: "mask" },
  block: { label: "Blocked", tone: "bad" },
  require_approval: { label: "Needs approval", tone: "wait" },
  executed: { label: "Operator action", tone: "neutral" },
  refused: { label: "Refused", tone: "bad" },
};

export const outcomeMeta = (o) => OUTCOMES[o] ?? { label: o ?? "unknown", tone: "neutral" };

export function spendRatio(agent) {
  const used = Number(agent.spend_used_today ?? 0), cap = Number(agent.spend_cap_daily ?? 0);
  return cap > 0 ? Math.min(1, used / cap) : 0;
}

export const titleCase = (s) => (s ?? "").replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
