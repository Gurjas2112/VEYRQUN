"""In-process policy evaluation: the compiled Rego (policy.wasm) with per-workspace data."""

from __future__ import annotations

import threading
from functools import lru_cache
from uuid import UUID

from ..config import get_settings
from .opa_wasm import OpaWasmPolicy

_lock = threading.Lock()
_engines: dict[UUID, tuple[int, OpaWasmPolicy]] = {}


@lru_cache(maxsize=1)
def wasm_bytes() -> bytes:
    return get_settings().policy_wasm_path.read_bytes()


def evaluate(workspace_id: UUID, revision: int, data: dict, input_doc: dict) -> dict:
    with _lock:
        hit = _engines.get(workspace_id)
        if hit is None or hit[0] != revision:
            policy = OpaWasmPolicy(wasm_bytes())
            policy.set_data({"policy_data": data})
            _engines[workspace_id] = hit = (revision, policy)
    decision = hit[1].evaluate(input_doc)
    if not isinstance(decision, dict) or "outcome" not in decision:
        # Undefined/malformed result: fail closed.
        return {"outcome": "block", "rule_id": "policy_undefined", "redactions": [], "limits": {},
                "mode": "enforce", "policy_version": 0}
    return decision


def reset() -> None:
    with _lock:
        _engines.clear()
