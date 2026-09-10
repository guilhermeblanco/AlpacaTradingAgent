"""The promotion gate, read for one challenger against one champion.

The gate itself already exists — `assess_promotion` applies the policy and
returns eligible, rejected, or insufficient data. What was missing is a way
to ask it about a specific pair of experiments from the workbench, and to
say honestly how much of the evidence came from replays rather than from
live cycles.

Two distinct hazards, and they are worth separating.

*Selection*: you choose which decisions to replay, so a challenger built
out of a hand-picked subset is biased however clean its sourcing is. That
is why replays are excluded by default.

*Leakage*: an episode whose sourcing was not fully date-bounded could see
information the original run could not. Since the live-search gate landed
that means a same-day replay, not a historical one — but the count is
reported either way, because episodes recorded before the gate existed are
still in the ledger.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from tradingagents.evaluation import PromotionDecision, PromotionPolicy, assess_promotion


class PromotionView(BaseModel):
    """A promotion verdict plus how it was arrived at.

    The three selections default to empty rather than being required. On a
    fresh deployment nothing has been recorded yet, so the dropdowns that
    feed this have no options and hand over None — and the whole point of
    `unavailable` below is to say so in a sentence. A model that cannot be
    constructed without the selections cannot carry the message explaining
    that they are missing.
    """

    challenger: str = ""
    champion: str = ""
    horizon: str = ""
    include_replays: bool = False
    decision: Optional[PromotionDecision] = None
    challenger_replays: int = 0
    champion_replays: int = 0
    challenger_unverified: int = 0
    unavailable: str = ""
    notes: list[str] = Field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.decision is not None

    @property
    def replay_share_pct(self) -> float:
        """How much of the challenger's evidence is replayed."""
        if self.decision is None or not self.decision.challenger.count:
            return 0.0
        return 100.0 * self.challenger_replays / self.decision.challenger.count


def _for_horizon(rows, horizon):
    return [row for row in rows or [] if row.horizon == horizon]


def _has_experiments(uow) -> bool:
    """Whether anything has been recorded to compare at all."""
    try:
        return bool(uow.evaluation.experiment_ids())
    except Exception:
        # Only used to choose between two messages; if the ledger cannot be
        # read the more general one is the safer of the two.
        return True


def build_promotion_view(
    uow,
    *,
    challenger: Optional[str],
    champion: Optional[str],
    horizon: Optional[str],
    policy: Optional[PromotionPolicy] = None,
    include_replays: bool = False,
) -> PromotionView:
    """Assess one challenger against one champion at one horizon."""
    # Normalised here, at the boundary, because the callers are Dash
    # dropdowns and an empty dropdown's value is None.
    challenger = challenger or ""
    champion = champion or ""
    horizon = horizon or ""

    view = PromotionView(
        challenger=challenger,
        champion=champion,
        horizon=horizon,
        include_replays=bool(include_replays),
    )
    if not challenger or not champion:
        # "Pick two" is unhelpful advice when there is nothing to pick from,
        # which is the state every new deployment starts in.
        view.unavailable = (
            "Pick a challenger and a champion to compare."
            if _has_experiments(uow)
            else "No experiment has recorded an outcome yet. Run an analysis, "
            "let the evaluation worker resolve it at a horizon, and this "
            "comparison fills in."
        )
        return view
    if challenger == champion:
        view.unavailable = "A variant compared against itself proves nothing."
        return view
    if not horizon:
        view.unavailable = "Pick a horizon."
        return view

    try:
        challenger_rows = _for_horizon(
            uow.evaluation.outcomes(
                experiment_id=challenger, include_replays=include_replays
            ),
            horizon,
        )
        champion_rows = _for_horizon(
            uow.evaluation.outcomes(
                experiment_id=champion, include_replays=include_replays
            ),
            horizon,
        )
    except Exception as exc:
        view.unavailable = f"Unable to read outcomes: {exc}"
        return view

    # Count how much of the challenger's evidence carries a caveat, so the
    # verdict can be read with that in mind.
    try:
        unverified = uow.evaluation.unverified_decision_ids()
        view.challenger_unverified = sum(
            1 for row in challenger_rows if row.decision_id in unverified
        )
    except Exception:
        pass

    if include_replays:
        try:
            live_challenger = _for_horizon(
                uow.evaluation.outcomes(
                    experiment_id=challenger, include_replays=False
                ),
                horizon,
            )
            live_champion = _for_horizon(
                uow.evaluation.outcomes(experiment_id=champion, include_replays=False),
                horizon,
            )
            view.challenger_replays = len(challenger_rows) - len(live_challenger)
            view.champion_replays = len(champion_rows) - len(live_champion)
        except Exception:
            pass

    try:
        view.decision = assess_promotion(
            challenger_rows, champion_rows, horizon=horizon, policy=policy
        )
    except Exception as exc:
        view.unavailable = f"Unable to assess promotion: {exc}"
        return view

    if view.challenger_replays:
        view.notes.append(
            f"{view.challenger_replays} of {view.decision.challenger.count} "
            "challenger outcomes came from replays. You chose which decisions "
            "to replay, so the sample is selected rather than drawn."
        )
    if view.challenger_unverified:
        view.notes.append(
            f"{view.challenger_unverified} challenger outcome(s) came from a "
            "run whose sources were not fully date-bounded, so they could see "
            "information the original decision could not."
        )
    if not include_replays:
        view.notes.append("Replayed outcomes are excluded from this comparison.")
    return view


def scorecard_rows(view: PromotionView) -> list[dict[str, Any]]:
    """The two scorecards side by side, for a table."""
    if view.decision is None:
        return []
    fields = (
        ("Outcomes", "count", "{:,.0f}"),
        ("Hit rate", "hit_rate_pct", "{:.1f}%"),
        ("Mean excess", "mean_excess_return_pct", "{:+.2f}%"),
        ("Excess LCB", "excess_return_lcb_pct", "{:+.2f}%"),
        ("Mean cost", "mean_cost_pct", "{:.2f}%"),
    )
    rows = []
    for label, attribute, fmt in fields:
        challenger = getattr(view.decision.challenger, attribute, None)
        champion = getattr(view.decision.champion, attribute, None)
        if challenger is None or champion is None:
            continue
        rows.append(
            {
                "label": label,
                "challenger": fmt.format(challenger),
                "champion": fmt.format(champion),
                "better": challenger > champion
                if attribute != "mean_cost_pct"
                else challenger < champion,
            }
        )
    return rows
