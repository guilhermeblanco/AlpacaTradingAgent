"""Board cards: persisted decisions and the run happening right now.

A decision only gets an id once the risk manager produces a typed intent,
so an analysis in flight has nothing in the database to show. The board
would therefore be blank during exactly the minutes an operator is most
likely to be watching it.

Live cards close that gap: the running analysis contributes cards derived
from in-process agent state, which are replaced by their persisted
counterparts as soon as the decision exists.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .tape import STAGES, DecisionTape, StageState

#: Which agents belong to which stage, so a live run can be placed.
AGENT_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "analyze",
        (
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
            "Macro Analyst",
        ),
    ),
    ("decide", ("Bull Researcher", "Bear Researcher", "Research Manager")),
    ("decide", ("Trader",)),
    (
        "decide",
        ("Risky Analyst", "Safe Analyst", "Neutral Analyst", "Portfolio Manager"),
    ),
)

BADGE_COLORS = {
    StageState.DONE.value: "success",
    StageState.RUNNING.value: "warning",
    StageState.BLOCKED.value: "danger",
    StageState.FAILED.value: "danger",
    StageState.SKIPPED.value: "secondary",
    StageState.PENDING.value: "secondary",
}


def card_from_tape(tape: DecisionTape) -> dict[str, Any]:
    """A persisted decision as a board card."""
    stage = tape.stage(tape.current_stage)
    state = stage.state if stage else StageState.PENDING
    halted = tape.halted_at
    return {
        "id": tape.decision_id,
        "symbol": tape.symbol,
        "stage": tape.current_stage,
        "stage_state": state.value,
        "badge": tape.status.upper(),
        "badge_color": "danger" if halted else BADGE_COLORS.get(state.value, "secondary"),
        "headline": (stage.headline if stage else "") or "—",
        "hint": tape.final_signal or "",
        "live": False,
        "at": tape.updated_at or tape.created_at,
    }


def _live_stage(statuses: dict[str, str]) -> tuple[str, str]:
    """Where a running analysis currently is, and what it is doing."""
    for stage_key, agents in AGENT_STAGES:
        for agent in agents:
            if statuses.get(agent) == "in_progress":
                return stage_key, agent
    completed = [
        stage_key
        for stage_key, agents in AGENT_STAGES
        if all(statuses.get(agent) == "completed" for agent in agents)
    ]
    if completed:
        return completed[-1], "waiting"
    return "gather", "starting"


def live_cards(
    symbol_states: dict[str, Any],
    *,
    analyzing_symbol: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Cards for the analysis running in this process, if any.

    Only symbols the run has actually started are included; a queued symbol
    with no agent activity has not entered the pipeline yet.
    """
    cards: list[dict[str, Any]] = []
    for symbol, state in (symbol_states or {}).items():
        statuses = (state or {}).get("agent_statuses") or {}
        if not any(
            value in ("in_progress", "completed") for value in statuses.values()
        ):
            continue
        stage_key, agent = _live_stage(statuses)
        done = sum(1 for value in statuses.values() if value == "completed")
        cards.append(
            {
                "id": f"live:{symbol}",
                "symbol": symbol,
                "stage": stage_key,
                "stage_state": StageState.RUNNING.value,
                "badge": "LIVE",
                "badge_color": "warning",
                "headline": (
                    f"{agent} running" if agent not in ("waiting", "starting")
                    else agent.title()
                ),
                "hint": f"{done} of {len(statuses)} agents done",
                "live": True,
                "at": None,
                "active": symbol == analyzing_symbol,
            }
        )
    return cards


def merge_cards(
    live: Iterable[dict[str, Any]], persisted: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Live cards first, minus any symbol the database already reflects.

    Once a decision exists the persisted card is the truthful one: it knows
    about gates and orders, which in-process agent state does not.
    """
    persisted = list(persisted)
    settled = {card["symbol"] for card in persisted}
    return [card for card in live if card["symbol"] not in settled] + persisted


def group_by_stage(cards: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key, _ in STAGES}
    for card in cards:
        grouped.setdefault(card["stage"], []).append(card)
    return grouped


def stage_counts(cards: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key, _ in STAGES}
    for card in cards:
        counts[card["stage"]] = counts.get(card["stage"], 0) + 1
    return counts


def halt_counts(tapes: Iterable[DecisionTape]) -> dict[str, int]:
    """How often each gate stopped a decision."""
    counter: Counter[str] = Counter()
    for tape in tapes:
        ledger = tape.gate_ledger
        if ledger is not None and ledger.blocked_by:
            gate = ledger.gate(ledger.blocked_by)
            counter[gate.label if gate else ledger.blocked_by] += 1
        elif tape.halted_at:
            stage = tape.stage(tape.halted_at)
            counter[stage.label if stage else tape.halted_at] += 1
    return dict(counter)


def throughput_series(
    tapes: Iterable[DecisionTape],
    *,
    hours: int = 12,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Decisions created per hour across the recent window.

    Every hour in the window is present, including the empty ones, so a
    quiet stretch reads as a gap rather than as missing data.
    """
    now = (now or datetime.now(timezone.utc)).replace(
        minute=0, second=0, microsecond=0
    )
    buckets = {now - timedelta(hours=offset): 0 for offset in range(hours - 1, -1, -1)}
    for tape in tapes:
        created = tape.created_at
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        hour = created.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        if hour in buckets:
            buckets[hour] += 1
    return [{"at": at, "count": count} for at, count in sorted(buckets.items())]
