"""Minimal OPA WebAssembly host for Python (wasmtime).

Runs the exact `policy.wasm` artifact the TypeScript gateway loads through
@open-policy-agent/opa-wasm, so the control plane and the data plane cannot drift.
Uses the ABI >= 1.2 fast path (`opa_eval`): one call per evaluation, input copied in as JSON.
Reference: https://www.openpolicyagent.org/docs/latest/wasm/
"""

from __future__ import annotations

import json
import threading
from typing import Any

from wasmtime import Engine, FuncType, Limits, Linker, Memory, MemoryType, Module, Store, ValType

PAGE = 65536


class OpaWasmError(RuntimeError):
    pass


class _Instance:
    """One wasm instance with `data` loaded. Not thread-safe; see OpaWasmPolicy."""

    def __init__(self, engine: Engine, module: Module, data_json: bytes) -> None:
        self.store = Store(engine)
        self.memory = Memory(self.store, MemoryType(Limits(2, None)))
        linker = Linker(engine)
        linker.define(self.store, "env", "memory", self.memory)
        i32 = ValType.i32()
        linker.define_func("env", "opa_abort", FuncType([i32], []), self._abort)
        linker.define_func("env", "opa_println", FuncType([i32], []), lambda addr: None)
        for arity in range(5):
            # The policy is built without host-only builtins; reaching one means the policy
            # changed in a way this host can't serve, so fail closed.
            linker.define_func(
                "env", f"opa_builtin{arity}", FuncType([i32] * (arity + 2), [i32]), self._missing_builtin
            )
        instance = linker.instantiate(self.store, module)
        self.exports = instance.exports(self.store)

        minor = self.exports["opa_wasm_abi_minor_version"].value(self.store)
        major = self.exports["opa_wasm_abi_version"].value(self.store)
        if major != 1 or minor < 2:
            raise OpaWasmError(f"unsupported OPA wasm ABI {major}.{minor}; need >= 1.2")

        required = json.loads(self._dump(self._call("builtins")))
        if required:
            raise OpaWasmError(f"policy needs host builtins {sorted(required)} which this host does not provide")
        self.entrypoints: dict[str, int] = json.loads(self._dump(self._call("entrypoints")))

        self.data_addr = self._parse_json(data_json)
        self.base_heap = self._call("opa_heap_ptr_get")

    def _call(self, name: str, *args: int) -> int:
        return self.exports[name](self.store, *args)

    def _abort(self, addr: int) -> None:
        raise OpaWasmError(f"opa_abort: {self._read_cstring(addr)}")

    def _missing_builtin(self, *args: int) -> int:
        raise OpaWasmError(f"policy invoked unsupported host builtin id={args[0]}")

    def _read_cstring(self, addr: int) -> str:
        mem_len = self.memory.data_len(self.store)
        end = addr
        chunk = 4096
        while end < mem_len:
            buf = self.memory.read(self.store, end, min(end + chunk, mem_len))
            nul = buf.find(0)
            if nul >= 0:
                end += nul
                break
            end += len(buf)
        return bytes(self.memory.read(self.store, addr, end)).decode("utf-8")

    def _dump(self, value_addr: int) -> str:
        return self._read_cstring(self._call("opa_json_dump", value_addr))

    def _ensure_capacity(self, needed_end: int) -> None:
        size = self.memory.data_len(self.store)
        if needed_end > size:
            self.memory.grow(self.store, (needed_end - size) // PAGE + 1)

    def _parse_json(self, raw: bytes) -> int:
        addr = self._call("opa_malloc", len(raw))
        self.memory.write(self.store, raw, addr)
        value = self._call("opa_json_parse", addr, len(raw))
        if value == 0:
            raise OpaWasmError("opa_json_parse failed for policy data")
        return value

    def evaluate(self, input_json: bytes, entrypoint: int) -> Any:
        input_addr = self.base_heap
        heap_after_input = input_addr + len(input_json)
        self._ensure_capacity(heap_after_input + PAGE)
        self.memory.write(self.store, input_json, input_addr)
        result_addr = self._call("opa_eval", 0, entrypoint, self.data_addr, input_addr, len(input_json), heap_after_input, 0)
        result = json.loads(self._read_cstring(result_addr))
        # Reset the heap so repeated evaluations don't grow memory without bound.
        self._call("opa_heap_ptr_set", self.base_heap)
        return result


class OpaWasmPolicy:
    """Thread-safe facade: one compiled module, one instance per (thread, data revision)."""

    def __init__(self, wasm: bytes, entrypoint: str = "veyrqun/authz/decision") -> None:
        self._engine = Engine()
        self._module = Module(self._engine, wasm)
        self._entrypoint_name = entrypoint
        self._local = threading.local()
        self._data_json = b"{}"
        self._revision = 0
        self._lock = threading.Lock()

    def set_data(self, data: dict) -> None:
        payload = json.dumps(data, separators=(",", ":")).encode()
        with self._lock:
            self._data_json = payload
            self._revision += 1

    def _instance(self) -> _Instance:
        inst: _Instance | None = getattr(self._local, "inst", None)
        if inst is None or self._local.revision != self._revision:
            with self._lock:
                data, revision = self._data_json, self._revision
            inst = _Instance(self._engine, self._module, data)
            if self._entrypoint_name not in inst.entrypoints:
                raise OpaWasmError(f"entrypoint {self._entrypoint_name!r} not in policy")
            self._local.inst, self._local.revision = inst, revision
        return inst

    def evaluate(self, input_doc: dict) -> Any:
        inst = self._instance()
        raw = json.dumps(input_doc, separators=(",", ":")).encode()
        results = inst.evaluate(raw, inst.entrypoints[self._entrypoint_name])
        if not results:
            return None
        return results[0].get("result")
