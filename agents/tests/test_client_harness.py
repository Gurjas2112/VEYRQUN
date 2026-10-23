"""Agent harness behaviour: what is retried, what never is."""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fleet.client import GatewayClient  # noqa: E402


def client(handler, **kw):
    sleeps: list[float] = []
    c = GatewayClient("http://gw", "http://cp", "id", "secret", transport=httpx.MockTransport(handler),
                      sleep=sleeps.append, **kw)
    c._token, c._token_exp = "t", 1e12  # skip the token call
    return c, sleeps


def test_model_calls_retry_transient_failures_and_honour_retry_after():
    codes = iter([503, 429, 200])

    def handler(req):
        code = next(codes)
        return httpx.Response(code, json={"choices": [{"message": {"content": "ok"}}]} if code == 200 else {},
                              headers={"retry-after": "2"} if code == 429 else {})
    c, sleeps = client(handler)
    assert c.chat([{"role": "user", "content": "hi"}]).kind == "ok"
    assert c.retries == 2 and 1.6 <= sleeps[1] <= 2.4  # second wait honours Retry-After (with jitter)


def test_policy_refusals_are_never_retried():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(403, json={"error": {"type": "policy_denied", "rule_id": "action_not_permitted"}})
    c, _ = client(handler)
    out = c.chat([{"role": "user", "content": "hi"}])
    assert out.kind == "refused" and out.rule_id == "action_not_permitted" and len(calls) == 1


def test_tool_calls_are_never_retried_even_on_503():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(503, json={"jsonrpc": "2.0", "id": 1, "error": {"data": {"type": "upstream_unavailable"}}})
    c, _ = client(handler)
    assert c.call_tool("waive_fee", {"amount": "1.00"}).kind == "refused" and len(calls) == 1


def test_retries_are_bounded():
    c, sleeps = client(lambda req: httpx.Response(503, json={}), max_retries=2)
    assert c.chat([{"role": "user", "content": "hi"}]).status == 503 and len(sleeps) == 2


def test_network_errors_on_model_calls_are_retried_then_raised():
    def handler(req):
        raise httpx.ConnectError("down")
    c, sleeps = client(handler, max_retries=1)
    with pytest.raises(httpx.ConnectError):
        c.chat([{"role": "user", "content": "hi"}])
    assert len(sleeps) == 1
