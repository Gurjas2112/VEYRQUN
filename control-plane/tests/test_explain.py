"""Every rule the policy or the counters can return has a plain-English reason (no generic fallbacks)."""

import re
from pathlib import Path

from veyrqun import explain

ROOT = Path(__file__).resolve().parents[2]


def _rule_ids() -> set[str]:
    rego = (ROOT / "policies" / "veyrqun" / "authz.rego").read_text()
    lua = "".join(p.read_text() for p in (ROOT / "policies" / "lua").glob("*.lua"))
    ids = set(re.findall(r'else := "([a-z_]+)"', rego)) | set(re.findall(r':= "([a-z_]+)" if', rego))
    ids |= set(re.findall(r"'([a-z_]+_exceeded)'", lua))
    return ids - {""}


def test_every_policy_and_counter_rule_is_explained():
    ids = _rule_ids()
    assert {"action_not_in_allowlist", "injection_suspected", "spend_cap_exceeded"} <= ids  # the parser found them
    missing = sorted(i for i in ids if i not in explain.REASONS)
    assert not missing, missing


def test_outcomes_win_over_rule_ids():
    assert explain.reason("redact", "data_class_redacted").startswith("Allowed with redaction")
    assert explain.reason("require_approval", "approval_required").startswith("Paused")
    assert explain.reason("allow", None).startswith("Allowed")
    assert explain.reason("block", "made_up_rule") == "Refused (made_up_rule)."
