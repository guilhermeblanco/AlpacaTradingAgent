"""The control-plane scope that gates execution for a broker account.

`ExecutionPipeline` refuses to trade while this scope is paused, and the
reconciliation worker and the account drift monitor are what pause it. Those
run as separate processes, so they must all derive the same string from the
same inputs: a quarantine written under one name while another process reads
a different one silently fails to stop anything.
"""

from __future__ import annotations

import os
from typing import Optional


def execution_quarantine_scope(
    broker: str,
    *,
    account_key: str = "",
    configured: Optional[str] = None,
) -> str:
    """Scope name for a broker account.

    An explicitly configured scope always wins, so operators sharing one
    account across processes can pin a single name.
    """
    if configured and configured.strip():
        return configured.strip()
    broker = (broker or "").strip()
    if not broker:
        raise ValueError("broker name is required to derive a quarantine scope")
    return f"execution:{broker}:{(account_key or '').strip()}".rstrip(":")


def quarantine_scope_from_env(broker: str) -> str:
    """Scope derived from the environment shared by the worker processes."""
    return execution_quarantine_scope(
        broker,
        account_key=os.getenv("AUTONOMOUS_ACCOUNT_KEY", ""),
        configured=os.getenv("EXECUTION_QUARANTINE_SCOPE"),
    )


def resolve_execution_scope(broker: str, *, configured: Optional[str] = None) -> str:
    """Scope for a process that holds a config dict rather than only env.

    Falls back to the environment the worker processes share, so the WebUI
    execution path lands on the same scope they do without extra setup.
    """
    if configured and str(configured).strip():
        return str(configured).strip()
    return quarantine_scope_from_env(broker)
