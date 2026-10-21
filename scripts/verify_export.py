"""Offline verification of a VEYRQUN signed audit export (for external auditors).

  python scripts/verify_export.py export.ndjson --jwks jwks.json      # or --jwks-url https://.../.well-known/jwks.json

Checks: manifest signature (Ed25519, kid from JWKS), body SHA-256, record count and first/last hash, every
entry's own SHA-256, and the hash links between entries that are consecutive in the global chain.
Needs only the export file, the public JWKS and Python + `cryptography`. No database access.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

HASHED_FIELDS = ("seq", "id", "ts", "workspace_id", "actor", "agent_id", "action_type", "amount", "currency",
                 "outcome", "mode", "rule_id", "policy_version", "decision_id", "prev_hash")


def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def entry_hash(e: dict) -> str:
    payload = json.dumps({k: e[k] for k in HASHED_FIELDS}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def verify(raw: bytes, jwks: dict) -> dict:
    lines = raw.rstrip(b"\n").split(b"\n")
    trailer = json.loads(lines[-1])
    body = b"".join(line + b"\n" for line in lines[:-1])
    manifest = trailer["manifest"]
    problems: list[str] = []

    jwk = next((k for k in jwks.get("keys", []) if k.get("kid") == trailer["kid"]), None)
    if jwk is None:
        problems.append(f"signing key {trailer['kid']} not in JWKS")
    else:
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        try:
            Ed25519PublicKey.from_public_bytes(_b64u(jwk["x"])).verify(_b64u(trailer["signature"]), canonical)
        except InvalidSignature:
            problems.append("manifest signature invalid")

    if hashlib.sha256(body).hexdigest() != manifest["body_sha256"]:
        problems.append("body does not match manifest body_sha256")
    entries = [json.loads(line) for line in lines[:-1]]
    if len(entries) != manifest["count"]:
        problems.append(f"count {len(entries)} != manifest {manifest['count']}")
    if entries and (entries[0]["entry_hash"] != manifest["first_hash"] or entries[-1]["entry_hash"] != manifest["last_hash"]):
        problems.append("first/last hash do not match manifest")

    bad_entries, links_checked = [], 0
    for i, e in enumerate(entries):
        if entry_hash(e) != e["entry_hash"]:
            bad_entries.append(e["id"])
        if i and entries[i - 1]["seq"] + 1 == e["seq"]:
            links_checked += 1
            if e["prev_hash"] != entries[i - 1]["entry_hash"]:
                problems.append(f"broken link at seq {e['seq']}")
    if bad_entries:
        problems.append(f"{len(bad_entries)} entries fail their own hash, first: {bad_entries[0]}")
    return {"valid": not problems, "entries": len(entries), "links_checked": links_checked, "problems": problems,
            "manifest": manifest}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", type=Path)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--jwks", type=Path)
    src.add_argument("--jwks-url")
    a = ap.parse_args()
    if a.jwks:
        jwks = json.loads(a.jwks.read_text())
    else:
        if not a.jwks_url.startswith("https://"):
            sys.exit("--jwks-url must be an https:// URL")
        # Operator-supplied, https-only URL for the public JWKS.
        with urllib.request.urlopen(a.jwks_url, timeout=10) as r:  # noqa: S310  # nosemgrep: dynamic-urllib-use-detected
            jwks = json.load(r)
    result = verify(a.export.read_bytes(), jwks)
    print(json.dumps({k: v for k, v in result.items() if k != "manifest"}, indent=2))
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
