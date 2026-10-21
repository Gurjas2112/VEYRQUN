/** Human-in-the-loop: pending approvals live in the control plane; the gateway creates, checks and consumes them. */
import { request } from "undici";

export interface ApprovalStatus {
  approval_id: string;
  status: "pending" | "approved" | "rejected" | "expired" | "consumed";
  agent_id: string;
  action_type: string;
  amount: string;
  currency: string;
}

export class ApprovalsClient {
  constructor(private readonly baseUrl: string, private readonly token: string) {}

  private async call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<{ status: number; json: T | null }> {
    const res = await request(`${this.baseUrl.replace(/\/$/, "")}${path}`, {
      method,
      headers: { authorization: `Bearer ${this.token}`, "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      headersTimeout: 3_000,
      bodyTimeout: 3_000,
    });
    const text = await res.body.text();
    return { status: res.statusCode, json: text ? (JSON.parse(text) as T) : null };
  }

  async create(ws: string, agentId: string, action: string, amount: string, currency: string, decisionId: string):
    Promise<{ approval_id: string; expires_at: string }> {
    const r = await this.call<{ approval_id: string; expires_at: string }>("POST", "/internal/approvals", {
      workspace_id: ws, agent_id: agentId, action_type: action, amount, currency, decision_id: decisionId,
    });
    if (r.status !== 201 || !r.json) throw new Error(`approval create failed (${r.status})`);
    return r.json;
  }

  async status(ws: string, approvalId: string): Promise<ApprovalStatus | null> {
    const r = await this.call<ApprovalStatus>("GET", `/internal/approvals/${encodeURIComponent(approvalId)}?workspace_id=${ws}`);
    return r.status === 200 ? r.json : null;
  }

  async consume(ws: string, approvalId: string, decisionId: string): Promise<boolean> {
    const r = await this.call("POST", `/internal/approvals/${encodeURIComponent(approvalId)}/consume`,
      { workspace_id: ws, decision_id: decisionId });
    return r.status === 200;
  }
}
