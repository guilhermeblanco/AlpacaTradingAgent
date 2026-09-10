"""Process-wide persistence runtime shared by readers and writers.

The operations cockpit refreshes every 15s and the decision explorer every
30s, for every open browser tab, and every analysis run wants to append its
stage record. Building a runtime per caller creates and disposes a
SQLAlchemy engine each time, so every one pays a fresh TCP connect and
authentication handshake and connection pooling never applies.

The runtime is cached until the configuration that produced it changes.
Callers must not close it: it is shared.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

_LOCK = threading.Lock()
_CACHED_KEY: Optional[tuple] = None
_CACHED_RUNTIME: Any = None


def runtime_key(config: dict) -> tuple:
    """Configuration a runtime is built from; a change rebuilds it."""
    return (
        str(config.get("persistence_backend") or "local").strip().lower(),
        str(config.get("database_url") or "").strip(),
        config.get("analysis_material_price_move_pct"),
        config.get("daily_llm_token_budget"),
    )


def get_persistence_runtime():
    """Return the shared persistence runtime for the current configuration."""
    import tradingagents.persistence as package
    from tradingagents.dataflows.config import get_config

    global _CACHED_KEY, _CACHED_RUNTIME

    config = get_config() or {}
    key = runtime_key(config)
    with _LOCK:
        if _CACHED_RUNTIME is not None and _CACHED_KEY == key:
            return _CACHED_RUNTIME
        _close_locked()
        _CACHED_RUNTIME = package.build_persistence_runtime(config)
        _CACHED_KEY = key
        return _CACHED_RUNTIME


def unit_of_work_factory():
    """The shared runtime's factory, or None when there is no database.

    Lets a caller that only wants to append a record stay entirely
    best-effort: no database configured is not an error.
    """
    try:
        return get_persistence_runtime().unit_of_work_factory
    except Exception:
        return None


def _close_locked() -> None:
    global _CACHED_KEY, _CACHED_RUNTIME
    if _CACHED_RUNTIME is not None:
        try:
            _CACHED_RUNTIME.close()
        except Exception:
            pass
    _CACHED_RUNTIME = None
    _CACHED_KEY = None


def reset_persistence_runtime() -> None:
    """Dispose the cached runtime; the next call rebuilds it."""
    with _LOCK:
        _close_locked()
