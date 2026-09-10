"""The analysis half of a decision, captured for the workbench.

Execution already leaves a durable trail: lifecycle rows, broker orders,
fills, outcomes, and now the gate ledger. Everything upstream of it — what
was gathered, what the analysts wrote, how the evidence scored, how the
debates resolved — existed only in the run log on disk and in prompt
strings, so the workbench could show what a decision *did* but never what
it was *made of*.

This records that half against the same `decision_id`, so one query
reconstructs the whole tape.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

ANALYSIS_STAGES_EVENT = "analysis_stages_recorded"

#: (state key, display label) for the five analyst reports, in graph order.
REPORTS: tuple[tuple[str, str], ...] = (
    ("market_report", "Market"),
    ("sentiment_report", "Social Sentiment"),
    ("news_report", "News"),
    ("fundamentals_report", "Fundamentals"),
    ("macro_report", "Macro"),
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _debate_side(state: Dict[str, Any], key: str) -> Dict[str, Any]:
    history = _text(state.get(key))
    return {"chars": len(history), "text": history}


def _gather_stage(state: Dict[str, Any]) -> Dict[str, Any]:
    """What the run started from: the candidate and its provenance."""
    provenance = state.get("candidate_provenance")
    return {
        "symbol": state.get("company_of_interest"),
        "trade_date": state.get("trade_date"),
        "current_position": state.get("current_position"),
        "provenance": provenance if isinstance(provenance, dict) else {},
    }


def _analyze_stage(state: Dict[str, Any]) -> Dict[str, Any]:
    """Which analysts produced a report, and how much of one."""
    reports = []
    for key, label in REPORTS:
        body = _text(state.get(key))
        reports.append(
            {
                "report_key": key,
                "label": label,
                "produced": bool(body.strip()),
                "chars": len(body),
            }
        )
    return {
        "reports": reports,
        "produced": sum(1 for item in reports if item["produced"]),
        "expected": len(reports),
    }


def _compute_stage(
    state: Dict[str, Any], config: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """The scored evidence the managers actually adjudicated on."""
    from tradingagents.agents.utils.report_context import build_decision_claim_matrix

    context = state.get("report_context")
    # "no evidence was found" and "the index was never built" are different
    # states; only the first one is worth charting as an empty matrix.
    if not isinstance(context, dict) or "evidence_claims" not in context:
        return {"available": False}
    try:
        matrix = build_decision_claim_matrix(context, config=config)
    except Exception:
        return {"available": False}
    return {"available": True, **matrix}


def _decide_stage(state: Dict[str, Any]) -> Dict[str, Any]:
    """Both debates, their round counts, and the verdicts they reached."""
    investment = state.get("investment_debate_state") or {}
    risk = state.get("risk_debate_state") or {}
    return {
        "research_debate": {
            "rounds": int(investment.get("count", 0) or 0),
            "bull": _debate_side(investment, "bull_history"),
            "bear": _debate_side(investment, "bear_history"),
            "transcript": _text(investment.get("history")),
            "verdict": _text(investment.get("judge_decision")),
        },
        "investment_plan": _text(state.get("investment_plan")),
        "trader_plan": _text(state.get("trader_investment_plan")),
        "risk_debate": {
            "rounds": int(risk.get("count", 0) or 0),
            "risky": _debate_side(risk, "risky_history"),
            "safe": _debate_side(risk, "safe_history"),
            "neutral": _debate_side(risk, "neutral_history"),
            "transcript": _text(risk.get("history")),
            "verdict": _text(risk.get("judge_decision")),
        },
        "final_decision": _text(state.get("final_trade_decision")),
        "recommended_action": state.get("recommended_action"),
        "trading_mode": state.get("trading_mode"),
    }


def build_analysis_record(
    final_state: Dict[str, Any],
    *,
    run_id: Optional[str] = None,
    final_signal: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the gather/analyze/compute/decide stages of one run."""
    intent = final_state.get("final_trade_intent")
    intent = intent if isinstance(intent, dict) else {}
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "decision_id": intent.get("decision_id"),
        "symbol": final_state.get("company_of_interest"),
        "trade_date": final_state.get("trade_date"),
        "final_signal": final_signal,
        "gather": _gather_stage(final_state),
        "analyze": _analyze_stage(final_state),
        "compute": _compute_stage(final_state, config),
        "decide": _decide_stage(final_state),
    }


def record_analysis_stages(record: Dict[str, Any]) -> bool:
    """Append the record against its decision id. Best effort.

    Returns whether it was written. A run without a typed intent has no
    decision to hang the record on, and a run without a database has
    nowhere to put it — neither is an error worth failing an analysis for.
    """
    decision_id = record.get("decision_id")
    if not decision_id:
        return False

    from tradingagents.persistence import unit_of_work_factory

    factory = unit_of_work_factory()
    if factory is None:
        return False
    try:
        with factory() as uow:
            uow.journal.append(
                ANALYSIS_STAGES_EVENT,
                symbol=str(record.get("symbol") or ""),
                decision_id=str(decision_id),
                run_id=record.get("run_id"),
                payload={"analysis": record},
            )
            uow.commit()
    except Exception:
        return False
    return True
