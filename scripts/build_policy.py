"""Compile policies/veyrqun/*.rego to the WebAssembly artifact that both the gateway and the
control plane evaluate in-process.

Usage:  python scripts/build_policy.py [--check]
--check fails (exit 1) if the committed policies/dist/policy.wasm is stale, which CI uses.

OPA embeds the source file path in the module, so the build must see the same relative POSIX path
on every machine. On Linux/macOS a local `opa` is used from the repo root; on Windows (backslash
paths) the official OPA container is used instead, so the committed bytes match CI exactly.
"""

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "policies" / "dist" / "policy.wasm"
ENTRYPOINT = "veyrqun/authz/decision"
OPA_VERSION = os.environ.get("OPA_VERSION", "1.21.3")
BUILD_ARGS = ["build", "-t", "wasm", "-e", ENTRYPOINT, "--ignore", "*_test.rego", "policies/veyrqun"]


def _use_container() -> bool:
    mode = os.environ.get("OPA_BUILD", "auto")
    return mode == "docker" or (mode == "auto" and platform.system() == "Windows")


def _local_opa() -> str:
    for candidate in (os.environ.get("OPA_BIN"), shutil.which("opa"), str(ROOT / "tools" / "bin" / "opa")):
        if candidate and Path(candidate).is_file():
            return candidate
    sys.exit("opa not found: install it, set OPA_BIN, or set OPA_BUILD=docker")


def build() -> bytes:
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        rel_out = Path(tmp).relative_to(ROOT).as_posix() + "/bundle.tar.gz"
        if _use_container():
            cmd = ["docker", "run", "--rm", "-v", f"{ROOT}:/src", "-w", "/src",
                   f"openpolicyagent/opa:{OPA_VERSION}-static", *BUILD_ARGS, "-o", rel_out]
        else:
            cmd = [_local_opa(), *BUILD_ARGS, "-o", rel_out]
        subprocess.run(cmd, check=True, cwd=ROOT)
        with tarfile.open(ROOT / rel_out, "r:gz") as tar:
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
