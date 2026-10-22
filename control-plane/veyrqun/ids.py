"""UUIDv7 (RFC 9562): time-ordered ids, so B-tree inserts stay append-mostly.

Monotonic within a process (RFC 9562 §6.2, method 2: increment the random part). Ids created in the same
millisecond still sort in creation order, so "ORDER BY created_at, id" is deterministic even when a whole batch
shares one transaction timestamp.
"""

import os
import threading
import time
import uuid

_RAND_B = (1 << 62) - 1
_lock = threading.Lock()
_last = 0


def _build(ms: int, rand: int) -> int:
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76                       # version
    value |= ((rand >> 62) & 0xFFF) << 64    # rand_a (12 bits)
    value |= 0b10 << 62                      # variant
    value |= rand & _RAND_B                  # rand_b (62 bits)
    return value


def uuid7() -> uuid.UUID:
    global _last
    ms = time.time_ns() // 1_000_000
    value = _build(ms, int.from_bytes(os.urandom(10), "big"))
    with _lock:
        if value <= _last:
            if _last & _RAND_B == _RAND_B:   # rand_b exhausted: move to the next millisecond
                value = _build((_last >> 80) + 1, int.from_bytes(os.urandom(10), "big"))
            else:
                value = _last + 1
        _last = value
    return uuid.UUID(int=value)
