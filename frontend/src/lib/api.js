// The browser only ever holds an HttpOnly session cookie (BFF). Unsafe methods carry the CSRF double-submit token.
export const BASE = (import.meta.env.VITE_API_BASE ?? "/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(status, message, data, headers) {
    super(message);
    this.status = status;
    this.data = data;
    this.headers = headers;
  }
}

function csrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)(?:__Host-)?veyrqun_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  const res = await fetch(BASE + path, {
    method, headers, signal, credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    const detail = data?.detail ?? data?.error?.message ?? data?.error?.type ?? res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail), data, res.headers);
  }
  return data;
}

export const ssoLoginUrl = (next = "/") => `${BASE}/auth/login?next=${encodeURIComponent(next)}`;

export async function download(path, filename) {
  const res = await fetch(BASE + path, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}
