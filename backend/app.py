"""Entrypoint shim so both `uvicorn app:app` (inside backend/) and
`uvicorn backend.app:app` (from the repo root) resolve the VEYRQUN package."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from veyrqun.main import app  # noqa: E402,F401
