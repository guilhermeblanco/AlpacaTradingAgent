"""Process-wide persistence runtime for WebUI callbacks.

Thin re-export: the cache now lives in `tradingagents.persistence.shared`
so analysis runs can append their stage records through the same engine the
callbacks read from, instead of each opening one of their own.
"""

from __future__ import annotations

from tradingagents.persistence.shared import (
    get_persistence_runtime,
    reset_persistence_runtime,
    runtime_key as _runtime_key,
)

__all__ = ["get_persistence_runtime", "reset_persistence_runtime"]
