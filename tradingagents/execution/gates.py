"""The ordered record of every gate a trade passed through.

Execution is a sequence of deliberate refusals: quarantine, intent shape,
snapshot freshness, plan semantics, broker capability, deterministic risk
sizing, and the safety layer. Each one either lets the order through,
clips its size, or stops it.

Until now the only thing that survived was the error string of whichever
gate happened to stop first, which makes "why didn't this trade?" an
archaeology exercise. The ledger keeps the whole sequence — including the
gates that passed and the numbers they passed on — so the answer is a
readable waterfall from requested notional to what was actually sent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class GateStatus(str, Enum):
    PASSED = "passed"
    CLIPPED = "clipped"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


#: Declared up front so the ledger renders every gate in pipeline order even
#: when execution stopped before reaching the later ones.
GATE_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("execution_quarantine", "Execution quarantine"),
    ("intent", "Intent shape"),
    ("snapshot", "Broker snapshot"),
    ("plan", "Plan semantics"),
    ("protection", "Protective orders"),
    ("risk_sizing", "Risk sizing"),
    ("safety", "Safety limits"),
    ("submission", "Broker submission"),
)

GATE_LABELS: dict[str, str] = dict(GATE_SEQUENCE)


class GateOutcome(BaseModel):
    """What one gate decided, and the numbers it decided on."""

    name: str
    label: str
    status: GateStatus
    reasons: list[str] = Field(default_factory=list)
    notional_before: Optional[float] = None
    notional_after: Optional[float] = None
    metrics: dict[str, Any] = Field(default_factory=dict)

    @property
    def clipped_by(self) -> Optional[float]:
        if self.notional_before is None or self.notional_after is None:
            return None
        difference = self.notional_before - self.notional_after
        return difference if difference > 1e-9 else None


class GateLedger(BaseModel):
    """The full sequence, in the order the pipeline evaluates it."""

    schema_version: str = SCHEMA_VERSION
    decision_id: str
    symbol: str
    requested_notional: float
    final_notional: Optional[float] = None
    gates: list[GateOutcome] = Field(default_factory=list)
    blocked_by: Optional[str] = None

    def _append(
        self,
        name: str,
        status: GateStatus,
        *,
        reasons: Optional[list[str]] = None,
        notional_before: Optional[float] = None,
        notional_after: Optional[float] = None,
        metrics: Optional[dict[str, Any]] = None,
    ) -> GateOutcome:
        outcome = GateOutcome(
            name=name,
            label=GATE_LABELS.get(name, name.replace("_", " ").title()),
            status=status,
            reasons=list(reasons or []),
            notional_before=notional_before,
            notional_after=notional_after,
            metrics=dict(metrics or {}),
        )
        self.gates.append(outcome)
        if status is GateStatus.BLOCKED and self.blocked_by is None:
            self.blocked_by = name
        if notional_after is not None:
            self.final_notional = notional_after
        return outcome

    def passed(self, name: str, **kwargs: Any) -> GateOutcome:
        return self._append(name, GateStatus.PASSED, **kwargs)

    def clipped(self, name: str, **kwargs: Any) -> GateOutcome:
        return self._append(name, GateStatus.CLIPPED, **kwargs)

    def blocked(self, name: str, **kwargs: Any) -> GateOutcome:
        return self._append(name, GateStatus.BLOCKED, **kwargs)

    def skipped(self, name: str, **kwargs: Any) -> GateOutcome:
        return self._append(name, GateStatus.SKIPPED, **kwargs)

    @property
    def allowed(self) -> bool:
        return self.blocked_by is None

    def gate(self, name: str) -> Optional[GateOutcome]:
        return next((item for item in self.gates if item.name == name), None)

    def waterfall(self) -> list[dict[str, Any]]:
        """Requested notional, each clip that reduced it, and what remained.

        The steps a chart needs: a starting bar, one negative bar per gate
        that took size off the table, and a final bar. A blocking gate
        removes whatever is left rather than an amount of its own.
        """
        steps: list[dict[str, Any]] = [
            {
                "label": "Requested",
                "kind": "start",
                "amount": self.requested_notional,
                "running": self.requested_notional,
            }
        ]
        running = self.requested_notional
        for outcome in self.gates:
            if outcome.status is GateStatus.BLOCKED:
                steps.append(
                    {
                        "label": outcome.label,
                        "kind": "blocked",
                        "amount": -running,
                        "running": 0.0,
                        "reasons": outcome.reasons,
                    }
                )
                running = 0.0
                break
            clipped = outcome.clipped_by
            if clipped:
                running = outcome.notional_after or 0.0
                steps.append(
                    {
                        "label": outcome.label,
                        "kind": "clip",
                        "amount": -clipped,
                        "running": running,
                        "reasons": outcome.reasons,
                    }
                )
        steps.append(
            {"label": "Sent", "kind": "end", "amount": running, "running": running}
        )
        return steps

    def summary(self) -> str:
        """One line for a card or a log."""
        if self.blocked_by:
            gate = self.gate(self.blocked_by)
            reason = "; ".join(gate.reasons) if gate and gate.reasons else "blocked"
            return f"Blocked at {GATE_LABELS.get(self.blocked_by, self.blocked_by)}: {reason}"
        final = self.final_notional
        if final is not None and final < self.requested_notional - 1e-9:
            return (
                f"Cleared with size reduced from ${self.requested_notional:,.0f} "
                f"to ${final:,.0f}"
            )
        return f"Cleared at ${self.requested_notional:,.0f}"


def ledger_from_payload(payload: Any) -> Optional[GateLedger]:
    """Rebuild a ledger from a persisted event payload, or None."""
    if isinstance(payload, GateLedger):
        return payload
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("gate_ledger", payload)
    if not isinstance(candidate, dict) or "gates" not in candidate:
        return None
    try:
        return GateLedger.model_validate(candidate)
    except Exception:
        return None
