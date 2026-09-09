"""The Decision Tape: one decision, all seven stages, in order.

A decision's record used to be split three ways — the analysis in a run log
on disk, execution in Postgres, outcomes in the evaluation ledger — so the
UI could show what a trade *did* but never what it was *made of*, and
never both at once.

The tape joins them on `decision_id` and presents them as the pipeline
actually runs: gather, analyze, compute, decide, prepare, order, react.
Every stage reports its own state, so a decision that stopped early shows
where and why rather than simply going quiet.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from tradingagents.execution.gates import GateLedger


class StageState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


#: (key, label) in pipeline order. The board and the tape share this so a
#: stage cannot appear in one and not the other.
STAGES: tuple[tuple[str, str], ...] = (
    ("gather", "Gather"),
    ("analyze", "Analyze"),
    ("compute", "Compute"),
    ("decide", "Decide"),
    ("prepare", "Prepare"),
    ("order", "Order"),
    ("react", "React"),
)

STAGE_LABELS: dict[str, str] = dict(STAGES)
STAGE_ORDER: dict[str, int] = {key: index for index, (key, _) in enumerate(STAGES)}

#: Lifecycle statuses that mean the decision is finished, one way or another.
TERMINAL_STATUSES = {"succeeded", "failed", "blocked", "expired", "cancelled"}


class TapeStage(BaseModel):
    """One stage's state, headline, and the detail behind it."""

    key: str
    label: str
    state: StageState = StageState.PENDING
    headline: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[datetime] = None

    @property
    def reached(self) -> bool:
        return self.state is not StageState.PENDING


class TapeEvent(BaseModel):
    occurred_at: datetime
    stage: str
    category: str
    label: str
    status: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionTape(BaseModel):
    decision_id: str
    symbol: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    broker: Optional[str] = None
    error: Optional[str] = None
    final_signal: Optional[str] = None
    trade_date: Optional[str] = None
    run_id: Optional[str] = None
    stages: list[TapeStage] = Field(default_factory=list)
    events: list[TapeEvent] = Field(default_factory=list)
    gate_ledger: Optional[GateLedger] = None
    analysis: dict[str, Any] = Field(default_factory=dict)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)

    def stage(self, key: str) -> Optional[TapeStage]:
        return next((item for item in self.stages if item.key == key), None)

    @property
    def current_stage(self) -> str:
        """Where a board card sits: the stage that stopped it, or the
        furthest one it actually entered.

        A skipped stage does not count as progress — a decision blocked at
        Prepare never reached Order, even though Order is recorded as
        "never submitted".
        """
        halted = self.halted_at
        if halted:
            return halted
        entered = [
            item.key
            for item in self.stages
            if item.state in (StageState.DONE, StageState.RUNNING)
        ]
        return entered[-1] if entered else STAGES[0][0]

    @property
    def halted_at(self) -> Optional[str]:
        """The stage that stopped it, or None if it is still moving."""
        for item in self.stages:
            if item.state in (StageState.BLOCKED, StageState.FAILED):
                return item.key
        return None

    @property
    def is_terminal(self) -> bool:
        return (
            self.status.lower() in TERMINAL_STATUSES or self.halted_at is not None
        )

    def evidence_bars(self) -> list[dict[str, Any]]:
        """Scoreboard dimensions as chartable rows."""
        scoreboard = (self.analysis.get("compute") or {}).get("scoreboard") or {}
        if not scoreboard:
            return []
        return [
            {"label": label, "value": float(scoreboard.get(key, 0.0) or 0.0)}
            for label, key in (
                ("Bullish", "bullish_score"),
                ("Bearish", "bearish_score"),
                ("Freshness", "freshness_score"),
                ("Quantitative", "quantitative_score"),
                ("Contradiction", "contradiction_score"),
                ("Source diversity", "source_diversity_score"),
            )
        ]

    def claim_rows(self) -> list[dict[str, Any]]:
        """Every scored claim, flattened for a table, best-scored first."""
        sources = (self.analysis.get("compute") or {}).get("sources") or []
        rows: list[dict[str, Any]] = []
        for source in sources:
            for claim in source.get("claims", []):
                rows.append({"source": source.get("label", ""), **claim})
        rows.sort(key=lambda row: row.get("confidence", 0.0), reverse=True)
        return rows


def _stage(key: str, **kwargs: Any) -> TapeStage:
    return TapeStage(key=key, label=STAGE_LABELS[key], **kwargs)


def _analysis_stages(analysis: dict[str, Any]) -> list[TapeStage]:
    """The four stages recorded by the graph, if it got that far."""
    if not analysis:
        return [_stage(key) for key, _ in STAGES[:4]]

    gather = analysis.get("gather") or {}
    analyze = analysis.get("analyze") or {}
    compute = analysis.get("compute") or {}
    decide = analysis.get("decide") or {}

    produced = int(analyze.get("produced", 0) or 0)
    expected = int(analyze.get("expected", 0) or 0)
    totals = compute.get("totals") or {}
    scoreboard = compute.get("scoreboard") or {}
    research = decide.get("research_debate") or {}
    risk = decide.get("risk_debate") or {}

    return [
        _stage(
            "gather",
            state=StageState.DONE,
            headline=(
                f"{gather.get('symbol', '')} on {gather.get('trade_date', '')}"
                f" from {gather.get('current_position', 'NEUTRAL')}"
            ).strip(),
            detail=gather,
        ),
        _stage(
            "analyze",
            state=StageState.DONE if produced else StageState.SKIPPED,
            headline=f"{produced} of {expected} analysts reported",
            detail=analyze,
        ),
        _stage(
            "compute",
            state=StageState.DONE if compute.get("available") else StageState.SKIPPED,
            headline=(
                f"{totals.get('claims', 0)} claims scored — net "
                f"{str(scoreboard.get('net_direction', 'mixed')).title()} "
                f"({scoreboard.get('net_confidence', 'low')} confidence)"
                if compute.get("available")
                else "No evidence index was built"
            ),
            detail=compute,
        ),
        _stage(
            "decide",
            state=StageState.DONE if decide.get("final_decision") else StageState.SKIPPED,
            headline=(
                f"{decide.get('recommended_action') or 'no action'} after "
                f"{research.get('rounds', 0)} research and "
                f"{risk.get('rounds', 0)} risk rounds"
            ),
            detail=decide,
        ),
    ]


def _prepare_stage(ledger: Optional[GateLedger]) -> TapeStage:
    if ledger is None:
        return _stage("prepare")
    if ledger.blocked_by and ledger.blocked_by != "submission":
        return _stage(
            "prepare",
            state=StageState.BLOCKED,
            headline=ledger.summary(),
            detail={"gate_ledger": ledger.model_dump(mode="json")},
        )
    return _stage(
        "prepare",
        state=StageState.DONE,
        headline=ledger.summary(),
        detail={"gate_ledger": ledger.model_dump(mode="json")},
    )


def _order_stage(
    ledger: Optional[GateLedger],
    orders: list[dict[str, Any]],
    status: str,
) -> TapeStage:
    if ledger is not None and ledger.blocked_by == "submission":
        gate = ledger.gate("submission")
        return _stage(
            "order",
            state=StageState.FAILED,
            headline="; ".join(gate.reasons) if gate and gate.reasons else "Rejected",
            detail={"orders": orders},
        )
    if not orders:
        if ledger is not None and ledger.blocked_by:
            return _stage("order", state=StageState.SKIPPED, headline="Never submitted")
        return _stage("order")
    filled = sum(float(order.get("filled_quantity", 0) or 0) for order in orders)
    return _stage(
        "order",
        state=StageState.DONE,
        headline=(
            f"{len(orders)} order(s), {filled:g} filled"
            if filled
            else f"{len(orders)} order(s) working"
        ),
        detail={"orders": orders, "status": status},
    )


def _react_stage(outcomes: list[dict[str, Any]]) -> TapeStage:
    if not outcomes:
        return _stage("react")
    correct = sum(1 for item in outcomes if item.get("directionally_correct"))
    best = max(
        (float(item.get("excess_return_pct", 0.0) or 0.0) for item in outcomes),
        default=0.0,
    )
    return _stage(
        "react",
        state=StageState.DONE,
        headline=(
            f"{len(outcomes)} horizon(s) resolved, {correct} directionally correct, "
            f"best excess {best:+.2f}%"
        ),
        detail={"outcomes": outcomes},
    )


def build_tape(
    *,
    summary: Any,
    analysis: Optional[dict[str, Any]] = None,
    gate_ledger: Optional[GateLedger] = None,
    orders: Optional[list[dict[str, Any]]] = None,
    outcomes: Optional[list[dict[str, Any]]] = None,
    events: Optional[list[TapeEvent]] = None,
) -> DecisionTape:
    """Assemble one tape from the pieces each layer persisted."""
    analysis = analysis or {}
    orders = orders or []
    outcomes = outcomes or []

    stages = _analysis_stages(analysis)
    stages.append(_prepare_stage(gate_ledger))
    stages.append(_order_stage(gate_ledger, orders, summary.status))
    stages.append(_react_stage(outcomes))

    return DecisionTape(
        decision_id=summary.decision_id,
        symbol=summary.symbol,
        status=summary.status,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        broker=summary.broker,
        error=summary.error,
        final_signal=analysis.get("final_signal"),
        trade_date=analysis.get("trade_date"),
        run_id=analysis.get("run_id"),
        stages=stages,
        events=sorted(events or [], key=lambda item: item.occurred_at),
        gate_ledger=gate_ledger,
        analysis=analysis,
        outcomes=outcomes,
    )


#: Which stage a persisted event belongs to, for grouping on the tape.
EVENT_STAGES: dict[str, str] = {
    "analysis_stages_recorded": "decide",
    "intent_received": "prepare",
    "execution_quarantined": "prepare",
    "validation_blocked": "prepare",
    "snapshot_failed": "prepare",
    "snapshot_loaded": "prepare",
    "intent_validated": "prepare",
    "plan_created": "prepare",
    "risk_blocked": "prepare",
    "risk_adjusted": "prepare",
    "plan_validated": "prepare",
    "safety_blocked": "prepare",
    "gate_ledger_recorded": "prepare",
    "broker_submitted": "order",
    "execution_submitted": "order",
    "execution_completed": "order",
    "execution_uncertain": "order",
    "broker_rejected": "order",
}


def stage_for_event(category: str, event_type: str) -> str:
    """Where an event sits on the tape."""
    if category == "outcome":
        return "react"
    if category in ("order", "fill"):
        return "order"
    return EVENT_STAGES.get(event_type, "prepare")
