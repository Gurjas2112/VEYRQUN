"""The only way fleet agents reach models and tools: the VEYRQUN gateway.

Agents hold no provider keys and no policy. This client authenticates (OAuth2 client credentials, short-lived
Ed25519 JWT) and translates the gateway's HTTP answers into outcomes the graph can route on:
200 -> ok, 202 -> approval pending, 4xx/5xx -> refused (with the policy rule_id when there is one).
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

Kind = Literal["ok", "approval", "refused"]


@dataclass
class Outcome:
    kind: Kind
    status: int
    body: dict[str, Any] = field(default_factory=dict)
    decision_id: str | None = None

    @property
    def rule_id(self) -> str | None:
        err = self.body.get("error")
        if not isinstance(err, dict):
            return None
        if isinstance(err.get("data"), dict):  # JSON-RPC error from /mcp carries the gateway error in `data`
            err = err["data"]
        if err.get("rule_id"):
            return err["rule_id"]
        if err.get("data_class"):  # the response was stopped by the output scan
            return f"response:{err['data_class']}"
        return err.get("type")

    @property
    def approval_id(self) -> str | None:
        return self.body.get("approval_id")


class GatewayClient:
    def __init__(self, gateway_url: str, control_plane_url: str, client_id: str, client_secret: str,
                 model: str = "mock-model", timeout: float = 30.0):
        self.gw = gateway_url.rstrip("/")
        self.cp = control_plane_url.rstrip("/")
        self._creds = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
        self.model = model
        self.http = httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._token_exp = 0.0
        self._ids = itertools.count(1)

    # ------------------------------------------------------------ auth

    def _bearer(self) -> dict[str, str]:
        if not self._token or time.time() > self._token_exp - 60:
            r = self.http.post(f"{self.cp}/oauth/token", json=self._creds)
            r.raise_for_status()
            body = r.json()
            self._token, self._token_exp = body["access_token"], time.time() + body["expires_in"]
        return {"authorization": f"Bearer {self._token}"}

    def _outcome(self, r: httpx.Response) -> Outcome:
        try:
            body = r.json()
        except ValueError:
            body = {"error": {"type": "non_json_response", "status": r.status_code}}
        kind: Kind = "ok" if r.status_code == 200 else "approval" if r.status_code == 202 else "refused"
        if r.status_code == 200 and isinstance(body, dict) and "error" in body and "jsonrpc" in body:
            kind = "refused"  # a JSON-RPC error with HTTP 200 (tool-level failure)
        return Outcome(kind, r.status_code, body, r.headers.get("x-veyrqun-decision-id"))

    # ------------------------------------------------------------ model

    def chat(self, messages: list[dict], tools: list[dict] | None = None, approval_id: str | None = None) -> Outcome:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": 256}
        if tools:
            body["tools"] = tools
        headers = self._bearer() | ({"x-veyrqun-approval": approval_id} if approval_id else {})
        return self._outcome(self.http.post(f"{self.gw}/v1/chat/completions", json=body, headers=headers))

    # ------------------------------------------------------------ tools (MCP over the gateway)

    def _rpc(self, method: str, params: dict | None = None, approval_id: str | None = None) -> Outcome:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or {}}
        headers = self._bearer() | ({"x-veyrqun-approval": approval_id} if approval_id else {})
        return self._outcome(self.http.post(f"{self.gw}/mcp", json=payload, headers=headers))

    def list_tools(self) -> list[dict]:
        """The tools this agent may call (the gateway filters tools/list), in OpenAI tool format."""
        out = self._rpc("tools/list")
        tools = (out.body.get("result") or {}).get("tools", []) if out.kind == "ok" else []
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("inputSchema") or {"type": "object"}}}
                for t in tools]

    def call_tool(self, name: str, arguments: dict, approval_id: str | None = None) -> Outcome:
        return self._rpc("tools/call", {"name": name, "arguments": arguments}, approval_id)

    def approval_status(self, approval_id: str) -> str | None:
        r = self.http.get(f"{self.gw}/v1/approvals/{approval_id}", headers=self._bearer())
        return r.json().get("status") if r.status_code == 200 else None

    def close(self) -> None:
        self.http.close()
