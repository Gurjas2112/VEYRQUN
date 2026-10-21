"""Phase 1 platform features: Rego/Lua parity, signed bundles, agent identity, approvals, money."""

import gzip
import io
import json
import os
import sys
import tarfile
from decimal import Decimal
from pathlib import Path

import jsonschema
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from veyrqun import ids, money, service
from veyrqun.policy.store import load_workspace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _b64u_decode(s: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- policy correctness


def test_rego_and_lua_agree_with_the_oracle_on_10k_random_cases():
    import policy_parity

    result = policy_parity.run(cases=10_000)
    assert result["mismatches"] == 0, result["first_mismatches"]
    # Every rule must actually be exercised, otherwise "0 mismatches" proves little.
    assert set(result["rule_distribution"]) >= {
        "None", "fleet_emergency_stop_active", "unknown_agent", "agent_revoked", "emergency_stop_active",
        "action_not_permitted", "action_not_in_allowlist", "data_class_not_permitted", "transaction_limit_exceeded",
        "injection_suspected", "approval_required", "rate_limit_exceeded", "spend_cap_exceeded",
        "action_spend_cap_exceeded", "data_class_redacted",
    }


def test_live_policy_data_matches_the_schema(ws):
    _, data, _ = load_workspace(ws)
    jsonschema.validate(data, json.loads((ROOT / "policies" / "data.schema.json").read_text()))


# ---------------------------------------------------------------- bundles


def test_bundle_is_deterministic_signed_and_cacheable(client):
    first = client.get("/bundles/current")
    second = client.get("/bundles/current")
    assert first.status_code == 200 and first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]

    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(first.content))) as tar:
        names = set(tar.getnames())
        data = json.load(tar.extractfile("data.json"))
    assert names == {".manifest", "data.json", "policy.wasm"}
    assert "travel_agent_01" in data["policy_data"]["agents"]

    kid = first.headers["x-veyrqun-signature-kid"]
    jwk = next(k for k in client.get("/.well-known/jwks.json").json()["keys"] if k["kid"] == kid)
    Ed25519PublicKey.from_public_bytes(_b64u_decode(jwk["x"])).verify(
        _b64u_decode(first.headers["x-veyrqun-signature"]), first.content)

    not_modified = client.get("/bundles/current", headers={"If-None-Match": first.headers["etag"]})
    assert not_modified.status_code == 304

    client.patch("/agents/travel_agent_01/policy", json={"spend_cap_daily": "1600"})
    changed = client.get("/bundles/current", headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200 and changed.headers["etag"] != first.headers["etag"]
    assert int(changed.headers["x-veyrqun-bundle-revision"]) > int(first.headers["x-veyrqun-bundle-revision"])


def test_bundle_requires_service_token_when_configured(client, monkeypatch):
    from veyrqun.config import get_settings

    monkeypatch.setattr(get_settings(), "bundle_service_token", "s3cret-token")
    assert client.get("/bundles/current").status_code == 401
    assert client.get("/bundles/current", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200


# ---------------------------------------------------------------- agent identity


def _register(client, agent_id="ops_agent_07"):
    res = client.post("/agents", json={"agent_id": agent_id, "display_name": "Ops", "allowed_actions": ["pay"],
                                       "spend_cap_daily": "100"})
    return res.json()["credentials"]


def test_client_credentials_issue_short_lived_eddsa_jwt_verifiable_via_jwks(client):
    creds = _register(client)
    res = client.post("/oauth/token", json={"grant_type": "client_credentials", **{k: creds[k] for k in ("client_id", "client_secret")}})
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    token = res.json()["access_token"]
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "EdDSA"
    jwk = next(k for k in client.get("/.well-known/jwks.json").json()["keys"] if k["kid"] == header["kid"])
    claims = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["EdDSA"], audience="veyrqun-gateway")
    assert claims["sub"] == "ops_agent_07" and claims["exp"] - claims["iat"] == 900


def test_bad_secret_and_revoked_agents_get_the_same_401(client):
    creds = _register(client)
    bad = {"grant_type": "client_credentials", "client_id": creds["client_id"], "client_secret": "vqs_wrong"}
    unknown = {**bad, "client_id": "default:nobody"}
    assert client.post("/oauth/token", json=bad).json() == client.post("/oauth/token", json=unknown).json()
    client.post("/agents/ops_agent_07/revoke")
    good = {"grant_type": "client_credentials", "client_id": creds["client_id"], "client_secret": creds["client_secret"]}
    assert client.post("/oauth/token", json=good).status_code == 401


def test_secret_rotation_invalidates_the_old_secret(client):
    creds = _register(client)
    new = client.post("/agents/ops_agent_07/rotate-secret").json()
    old_req = {"grant_type": "client_credentials", "client_id": creds["client_id"], "client_secret": creds["client_secret"]}
    assert client.post("/oauth/token", json=old_req).status_code == 401
    assert client.post("/oauth/token", json={**old_req, "client_secret": new["client_secret"]}).status_code == 200


def test_signing_keys_are_encrypted_at_rest(owner_engine):
    from sqlalchemy import text

    service.current_bundle("default")
    with owner_engine.connect() as conn:
        blob = conn.execute(text("SELECT private_enc FROM iam.signing_keys LIMIT 1")).scalar_one()
    assert bytes(blob).startswith(b"v1.") and b"PRIVATE KEY" not in bytes(blob)


# ---------------------------------------------------------------- approvals (human in the loop)


def test_require_approval_flow_with_maker_checker(client):
    first = client.post("/simulate/action", json={"agent_id": "credit_limit_03", "action_type": "credit_limit_increase",
                                                  "amount": "2100"})
    assert first.status_code == 202
    body = first.json()
    assert body["outcome"] == "require_approval" and body["approval_id"]
    approval_id = body["approval_id"]
    assert [a["approval_id"] for a in client.get("/approvals").json()] == [approval_id]

    # Retrying with the approval before it's decided is refused.
    retry = {"agent_id": "credit_limit_03", "action_type": "credit_limit_increase", "amount": "2100", "approval_id": approval_id}
    assert client.post("/simulate/action", json=retry).status_code == 409

    # The agent that raised it can't approve it (maker-checker), whoever calls the service.
    with pytest.raises(service.GovernanceError) as exc:
        service.decide_approval(approval_id, True, actor="agent:credit_limit_03")
    assert exc.value.status_code == 403

    assert client.post(f"/approvals/{approval_id}/approve", json={"reason": "verified income"}).json()["status"] == "approved"
    done = client.post("/simulate/action", json=retry).json()
    assert done["outcome"] == "allow"
    # One-shot: the approval is consumed and cannot be replayed.
    assert client.post("/simulate/action", json=retry).status_code == 409
    # A different amount does not ride on someone else's approval.
    other = client.post("/simulate/action", json={**retry, "amount": "2200"})
    assert other.status_code == 409


def test_rejected_approval_cannot_be_used(client):
    approval_id = client.post("/simulate/action", json={"agent_id": "credit_limit_03", "action_type": "credit_limit_increase",
                                                        "amount": "2000"}).json()["approval_id"]
    client.post(f"/approvals/{approval_id}/reject")
    retry = {"agent_id": "credit_limit_03", "action_type": "credit_limit_increase", "amount": "2000", "approval_id": approval_id}
    assert client.post("/simulate/action", json=retry).status_code == 409


# ---------------------------------------------------------------- fail closed


def test_redis_down_fails_closed(client, monkeypatch):
    from veyrqun import counters

    def boom(*args, **kwargs):
        raise counters.CounterUnavailable("connection refused")

    monkeypatch.setattr(counters, "reserve", boom)
    entry = client.post("/simulate/action", json={"agent_id": "travel_agent_01", "action_type": "flight_rebook",
                                                  "amount": "10"}).json()
    assert entry["outcome"] == "block" and entry["rule_id"] == "state_store_unavailable"


def test_observe_mode_records_would_be_block_but_allows(client):
    service.create_agent({"agent_id": "shadow_agent", "display_name": "Shadow", "allowed_actions": ["pay"],
                          "blocked_actions": [], "spend_cap_daily": Decimal("10"), "mode": "observe"})
    res = client.post("/simulate/action", json={"agent_id": "shadow_agent", "action_type": "refund", "amount": "1"}).json()
    assert res["outcome"] == "block" and res["mode"] == "observe"
    assert res["enforced"] is False and res["effective_outcome"] == "allow"


# ---------------------------------------------------------------- primitives


def test_money_is_exact_and_rejects_floats_precision():
    assert money.to_minor(Decimal("0.1") + Decimal("0.2")) == 30
    assert money.to_minor(Decimal("1500.00")) == 150_000
    assert money.from_minor(1, "USD") == Decimal("0.01")
    assert money.to_minor(Decimal("12"), "JPY") == 12
    with pytest.raises(money.MoneyError):
        money.to_decimal("1.001", "USD")
    with pytest.raises(money.MoneyError):
        money.to_decimal("NaN")


def test_uuid7_is_time_ordered_and_versioned():
    a, b = ids.uuid7(), ids.uuid7()
    assert a.version == 7 and a.variant == "specified in RFC 4122"
    assert a.int >> 80 <= b.int >> 80


@pytest.mark.skipif(os.environ.get("VEYRQUN_ENV") != "test", reason="config is env-driven")
def test_production_config_refuses_insecure_defaults():
    from pydantic import ValidationError

    from veyrqun.config import Settings

    with pytest.raises(ValidationError):
        Settings(veyrqun_env="production")
