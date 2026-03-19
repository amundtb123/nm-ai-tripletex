"""Per-request correlation id for logs (set in /solve, read in Tripletex helpers)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

solve_request_id: ContextVar[Optional[str]] = ContextVar("solve_request_id", default=None)
