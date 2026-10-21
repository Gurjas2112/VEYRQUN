"""Compile policies/veyrqun/*.rego to the WebAssembly artifact that both the gateway and the
control plane evaluate in-process.

Usage:  python scripts/build_policy.py [--check]
--check fails (exit 1) if the committed policies/dist/policy.wasm is stale, which CI uses.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "policies" / "dist" / "policy.wasm"
ENTRYPOINT = "veyrqun/authz/decision"


def opa_binary() -> str:
    for candidate in (os.environ.get("OPA_BIN"), str(ROOT / "tools" / "bin" / "opa.exe"), str(ROOT / "tools" / "bin" / "opa")):
        if candidate and Path(candidate).is_file():
            return candidate
    found = shutil.which("opa")
    if not found:
        sys.exit("opa not found: set OPA_BIN or put it on PATH")
    return found


def build() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bundle.tar.gz"
        subprocess.run(
            [opa_binary(), "build", "-t", "wasm", "-e", ENTRYPOINT, "--ignore", "*_test.rego",
             "-o", str(out), str(ROOT / "policies" / "veyrqun")],
            check=True,
        )
        with tarfile.open(out, "r:gz") as tar:
            member = tar.extractfile("/policy.wasm")
            assert member is not None
            return member.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    wasm = build()
    digest = hashlib.sha256(wasm).hexdigest()
    if args.check:
        current = DIST.read_bytes() if DIST.exists() else b""
        if hashlib.sha256(current).hexdigest() != digest:
            sys.exit("policies/dist/policy.wasm is stale; run: python scripts/build_policy.py")
        print(f"policy.wasm up to date ({digest[:12]})")
        return
    DIST.parent.mkdir(parents=True, exist_ok=True)
    DIST.write_bytes(wasm)
    (DIST.parent / "policy.wasm.sha256").write_text(digest + "\n")
    print(f"wrote {DIST.relative_to(ROOT)} ({len(wasm)} bytes, sha256 {digest[:12]})")


if __name__ == "__main__":
    main()
