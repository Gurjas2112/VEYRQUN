"""Signed policy bundles for gateways: OPA bundle layout (policy.wasm + data.json + .manifest)
as a byte-for-byte deterministic tar.gz, so the ETag is a pure content hash.

Signature: Ed25519 over the bundle bytes, sent as headers (X-Veyrqun-Signature, -Kid).
Gateways verify it against JWKS before hot-swapping (ADR 0004).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile

from .. import keys
from .engine import wasm_bytes
from .store import canonical_data_json


def _add(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = 0
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    tar.addfile(info, io.BytesIO(payload))


def build(revision: int, data: dict) -> tuple[bytes, str]:
    manifest = {
        "revision": str(revision),
        "roots": ["veyrqun", "policy_data"],
        "wasm": [{"entrypoint": "veyrqun/authz/decision", "module": "/policy.wasm"}],
        "metadata": {"policy_sha256": hashlib.sha256(wasm_bytes()).hexdigest()},
    }
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        _add(tar, ".manifest", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
        _add(tar, "data.json", canonical_data_json(data))
        _add(tar, "policy.wasm", wasm_bytes())
    gz = gzip.compress(raw.getvalue(), mtime=0)
    return gz, hashlib.sha256(gz).hexdigest()


def sign(bundle: bytes) -> tuple[str, str]:
    return keys.sign_bytes("bundle", bundle)
