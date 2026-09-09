"""Preview, replay, and operator overrides.

Three things the backend already supported and the UI never exposed.

**Preview** runs a decision through the whole prepare stage against a
dry-run gateway: every gate is evaluated against live account state, a gate
ledger comes back, and nothing reaches a broker. It answers "what would
this do right now?" without finding out the expensive way.

**Replay** does the same for a decision that already happened, optionally
at a different size, so the original ledger and the hypothetical one can be
read side by side. It does not re-run the analysts — that would cost real
money and minutes — it re-runs the deterministic half, which is the half
that decides how much gets sent.

**Overrides** let an operator lift a quarantine. That is a consequential
act, so it is recorded with who did it and why.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from tradingagents.agents.schemas import TradeIntent
from tradingagents.execution.gates import GateLedger, ledger_from_payload

OVERRIDE_EVENT = "operator_override_recorded"


class PreviewUnavailable(RuntimeError):
    """The preview could not be run at all, as opposed to being refused."""


def preview_intent(
    intent: TradeIntent | dict[str, Any],
    requested_notional: float,
    *,
    config_overrides: Optional[dict[str, Any]] = None,
    execute=None,
) -> dict[str, Any]:
    """Evaluate every gate against live state without sending an order.

    The dry-run gateway is forced regardless of configuration, and
    persistence is left out: a preview must not create a lifecycle record
    that a later real attempt would then collide with.
    """
    from tradingagents.dataflows.config import get_config
    from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
    from tradingagents.execution.pipeline import execute_autonomous_trade

    try:
        parsed = (
            intent
            if isinstance(intent, TradeIntent)
            else TradeIntent.model_validate(intent)
        )
    except Exception as exc:
        raise PreviewUnavailable(f"Invalid trade intent: {exc}") from exc

    config = copy.deepcopy(get_config() or {})
    config.update(config_overrides or {})

    runner = execute or execute_autonomous_trade
    result = runner(
        parsed.symbol,
        parsed,
        float(requested_notional or 0.0),
        gateway=DryRunExecutionGateway(),
        # No unit of work: a preview leaves no lifecycle record behind.
        unit_of_work_factory=_no_persistence,
        lifecycle=None,
    )
    result["preview"] = True
    return result


def _no_persistence():  # pragma: no cover - never entered; presence is the point
    raise AssertionError("a preview must not write persistence records")


def replay_tape(
    tape,
    *,
    requested_notional: Optional[float] = None,
    config_overrides: Optional[dict[str, Any]] = None,
    execute=None,
) -> dict[str, Any]:
    """Re-run a recorded decision's deterministic half, optionally resized."""
    intent = getattr(tape, "intent", None)
    if not intent:
        raise PreviewUnavailable(
            "The original trade intent was not recorded for this decision."
        )
    if requested_notional is None:
        ledger = tape.gate_ledger
        requested_notional = ledger.requested_notional if ledger else 0.0
    return preview_intent(
        intent,
        requested_notional,
        config_overrides=config_overrides,
        execute=execute,
    )


def compare_ledgers(
    original: Optional[GateLedger], candidate: Optional[GateLedger]
) -> list[dict[str, Any]]:
    """Gate-by-gate difference between what happened and what would happen."""
    if candidate is None:
        return []
    by_name = {gate.name: gate for gate in (original.gates if original else [])}
    rows: list[dict[str, Any]] = []
    for gate in candidate.gates:
        before = by_name.get(gate.name)
        rows.append(
            {
                "gate": gate.name,
                "label": gate.label,
                "was": before.status.value if before else None,
                "now": gate.status.value,
                "changed": before is None or before.status is not gate.status,
                "reasons": gate.reasons,
                "notional_after": gate.notional_after,
            }
        )
    return rows


def resume_scope(
    unit_of_work_factory,
    scope: str,
    *,
    actor: str,
    reason: str = "",
) -> dict[str, Any]:
    """Lift a quarantine, recording who did it and why.

    A quarantine exists because something disagreed — an account drift, a
    tripped breaker. Lifting one by hand is a decision in its own right and
    is journalled as one.
    """
    scope = str(scope or "").strip()
    if not scope:
        raise ValueError("a scope is required")
    actor = str(actor or "").strip()
    if not actor:
        raise ValueError("an actor is required")

    with unit_of_work_factory() as uow:
        control = uow.operations.set_paused(
            scope, paused=False, reason=None, updated_by=actor
        )
        uow.journal.append(
            OVERRIDE_EVENT,
            symbol="",
            decision_id=f"override:{scope}",
            run_id=None,
            payload={
                "override": "resume_scope",
                "scope": scope,
                "actor": actor,
                "reason": reason,
            },
        )
        uow.commit()
    return {
        "scope": scope,
        "paused": control.paused,
        "actor": actor,
        "reason": reason,
    }


def preview_ledger(result: dict[str, Any]) -> Optional[GateLedger]:
    """The gate ledger out of a preview result, if it produced one."""
    return ledger_from_payload(result)
