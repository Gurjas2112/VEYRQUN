export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export const WS_URL = API_BASE.replace(/^http/, 'ws') + '/ws/live-feed';

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join('; ') : data?.detail;
    throw new Error(detail || `${method} ${path} failed (${res.status})`);
  }
  return data;
}

export const isOperatorEvent = (entry) => entry.action_type?.startsWith('operator:');

export const formatMoney = (value) => (value === null || value === undefined ? '—' : `$${Number(value).toFixed(2)}`);
