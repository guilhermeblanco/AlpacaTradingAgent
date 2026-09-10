"""Re-run a recorded decision under an experiment variant.

The gate-only preview answers "would this still trade, and at what size?".
This answers the larger question: *would a different configuration have
decided differently, and would it have been better?* That means actually
re-running the analysts under the variant's config overrides, recording the
result as a shadow episode tagged with the experiment, and letting the
existing evaluation worker resolve it into outcomes the promotion gate can
compare.

Three constraints shape this, and all three are load-bearing:

**A replay never executes.** It produces a shadow episode and nothing else.
No gateway is constructed and no intent reaches the execution pipeline.

**A replay measures from the original decision's timestamp**, not from now.
Comparing a challenger scored from today against a champion scored from
three days ago would not be a comparison.

**A replay of a past date uses only date-bounded sources.** Every dated
source honours the window, and the hosted web-search tools now stand down
for a historical date rather than searching against a window they cannot
enforce (see `dataflows.search_window`). Episodes still record whether the
sourcing was bounded, so a run made before that gate existed — or one whose
date could not be read — stays distinguishable, and the promotion gate can
exclude them.
"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from tradingagents.evaluation import ExperimentVariant

#: Marks an episode as produced by a replay rather than by a live cycle.
REPLAY_SOURCE = "workbench_replay"

#: Only exposure-adding actions can be scored; a HOLD has nothing to measure.
SCOREABLE_ACTIONS = {"BUY", "OPEN", "INCREASE", "LONG", "SHORT"}

DEFAULT_ANALYSTS = ("market", "social", "news", "fundamentals", "macro")


class ReplayRefused(RuntimeError):
    """The replay was not attempted, and why."""


class ReplayRequest(BaseModel):
    origin_decision_id: str
    symbol: str
    trade_date: str
    experiment_id: str
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    analysts: list[str] = Field(default_factory=lambda: list(DEFAULT_ANALYSTS))
    #: The original decision's timestamp; the challenger is scored from the
    #: same point so the two are comparable.
    decision_at: Optional[datetime] = None


class ReplayOutcome(BaseModel):
    request: ReplayRequest
    replay_decision_id: str
    action: Optional[str] = None
    episode_recorded: bool = False
    note: str = ""
    point_in_time_verified: bool = True
    started_at: datetime
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def variants_from_env() -> list[ExperimentVariant]:
    """The variants the autonomous worker would assign, for the UI to offer.

    Same environment variable and same default, so what you can replay under
    is what the machine would actually run.
    """
    import json
    import os

    raw = os.getenv(
        "AUTONOMOUS_EXPERIMENTS_JSON",
        '[{"experiment_id":"champion","weight":1,'
        '"execution_eligible":true,"config_overrides":{}}]',
    )
    try:
        rows = json.loads(raw)
    except Exception:
        return [ExperimentVariant(experiment_id="champion", execution_eligible=True)]
    variants = []
    for row in rows if isinstance(rows, list) else []:
        try:
            variants.append(ExperimentVariant.model_validate(row))
        except Exception:
            continue
    return variants or [
        ExperimentVariant(experiment_id="champion", execution_eligible=True)
    ]


def _sources_are_bounded(trade_date: str, now: datetime) -> bool:
    """Whether this replay could only see what the original run could.

    Asks the same gate the tools consult rather than inferring it from the
    date, so the marker cannot drift away from the behaviour.

    A *historical* replay is clean: every source honours the window and the
    hosted search stands down. A *same-day* replay is not — the search runs
    live, and the original decision was made earlier in the day, so anything
    published since is visible to the challenger and was not to the
    champion. Windows here are day-granular and do not model the hour.
    """
    from tradingagents.dataflows.search_window import (
        live_search_allowed,
        parse_analysis_date,
    )

    if parse_analysis_date(trade_date) is None:
        return False
    return not live_search_allowed(trade_date, now=now)


def run_variant_replay(
    request: ReplayRequest,
    *,
    graph_factory: Optional[Callable[..., Any]] = None,
    prices: Optional[Any] = None,
    unit_of_work_factory: Optional[Callable[[], Any]] = None,
    now: Optional[datetime] = None,
) -> ReplayOutcome:
    """Run the analysts under a variant and record the result as a shadow.

    Returns an outcome rather than raising for the ordinary "nothing to
    score" cases: a HOLD, or an unresolvable reference price, are results,
    not faults.
    """
    from tradingagents.agents.schemas import trade_intent_action

    now = now or datetime.now(timezone.utc)
    replay_decision_id = f"replay-{uuid.uuid4().hex[:12]}"
    outcome = ReplayOutcome(
        request=request,
        replay_decision_id=replay_decision_id,
        started_at=now,
        point_in_time_verified=_sources_are_bounded(request.trade_date, now),
    )

    try:
        state = _propagate(request, graph_factory)
    except Exception as exc:
        outcome.error = f"The replay run failed: {exc}"
        outcome.finished_at = datetime.now(timezone.utc)
        return outcome

    intent = state.get("final_trade_intent")
    intent = intent if isinstance(intent, dict) else {}
    action = (trade_intent_action(intent) or "").upper() or None
    outcome.action = action

    if action not in SCOREABLE_ACTIONS:
        outcome.note = (
            f"The variant decided {action or 'nothing'}, which adds no exposure "
            "and so has nothing to score."
        )
        outcome.finished_at = datetime.now(timezone.utc)
        _record_stages(state, replay_decision_id, request)
        return outcome

    try:
        _record_episode(
            outcome,
            state=state,
            intent=intent,
            action=action,
            prices=prices,
            unit_of_work_factory=unit_of_work_factory,
        )
    except Exception as exc:
        outcome.note = f"The run finished but no episode could be recorded: {exc}"
    else:
        outcome.episode_recorded = True

    _record_stages(state, replay_decision_id, request)
    outcome.finished_at = datetime.now(timezone.utc)
    return outcome


def _propagate(request: ReplayRequest, graph_factory) -> dict[str, Any]:
    """Run the graph under the variant's configuration."""
    from tradingagents.dataflows.config import get_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = copy.deepcopy(get_config() or {})
    config.update(request.config_overrides or {})
    factory = graph_factory or TradingAgentsGraph
    graph = factory(
        list(request.analysts or DEFAULT_ANALYSTS), config=config, debug=False
    )
    state, _signal = graph.propagate(request.symbol, request.trade_date)
    return state if isinstance(state, dict) else {}


def _record_episode(
    outcome: ReplayOutcome,
    *,
    state: dict[str, Any],
    intent: dict[str, Any],
    action: str,
    prices,
    unit_of_work_factory,
) -> None:
    import os

    from tradingagents.evaluation import (
        build_signal_episode,
        default_historical_price_registry,
    )
    from tradingagents.persistence import unit_of_work_factory as shared_factory

    request = outcome.request
    prices = prices or default_historical_price_registry().create(
        os.getenv("EVALUATION_PRICE_PROVIDER", "alpaca")
    )
    decision_at = request.decision_at or outcome.started_at

    episode = build_signal_episode(
        outcome.replay_decision_id,
        symbol=request.symbol,
        action=action,
        decision_at=decision_at,
        prices=prices,
        benchmark_symbol=os.getenv("EVALUATION_BENCHMARK_SYMBOL", "SPY"),
        confidence=intent.get("confidence_score"),
        experiment_id=request.experiment_id,
        metadata={
            "experiment_role": "shadow",
            # `build_signal_episode` sets "source" itself, so the replay
            # marker needs a key of its own.
            "origin": REPLAY_SOURCE,
            "origin_decision_id": request.origin_decision_id,
            "trade_date": request.trade_date,
            # A replay of a past date can see information the original run
            # could not; the promotion gate can exclude these.
            "point_in_time_verified": outcome.point_in_time_verified,
        },
    )

    factory = unit_of_work_factory or shared_factory()
    if factory is None:
        raise ReplayRefused("no database is configured to record the episode")
    with factory() as uow:
        uow.evaluation.record_episode(episode)
        uow.commit()


def _record_stages(
    state: dict[str, Any], replay_decision_id: str, request: ReplayRequest
) -> None:
    """Give the replay its own tape, keyed to the replay's decision id."""
    try:
        from .analysis_record import build_analysis_record, record_analysis_stages

        record = build_analysis_record(state, run_id=None)
        record["decision_id"] = replay_decision_id
        record["symbol"] = request.symbol
        record["replay_of"] = request.origin_decision_id
        record["experiment_id"] = request.experiment_id
        record_analysis_stages(record)
    except Exception:
        # The replay's own result is what matters; its tape is a convenience.
        pass


class ReplayJob(BaseModel):
    job_id: str
    request: ReplayRequest
    status: str = "queued"
    queued_at: datetime
    outcome: Optional[ReplayOutcome] = None
    error: Optional[str] = None

    @property
    def finished(self) -> bool:
        return self.status in ("done", "failed", "refused")


class ReplayJobs:
    """Runs replays one at a time, off the request thread.

    Serialized deliberately. `Toolkit` publishes tool configuration at class
    level, so two graphs running different variants at once would overwrite
    each other's configuration — which is also why a replay refuses to start
    while a live analysis is in progress.
    """

    def __init__(self, *, runner=None, max_history: int = 20):
        self._lock = threading.Lock()
        self._jobs: dict[str, ReplayJob] = {}
        self._order: list[str] = []
        self._running: Optional[str] = None
        self._runner = runner or run_variant_replay
        self._max_history = max_history

    @property
    def running(self) -> Optional[str]:
        with self._lock:
            return self._running

    def submit(self, request: ReplayRequest, *, analysis_running: bool = False) -> ReplayJob:
        if analysis_running:
            raise ReplayRefused(
                "An analysis is already running. Tool configuration is shared "
                "process-wide, so a replay would corrupt it."
            )
        with self._lock:
            if self._running is not None:
                raise ReplayRefused("A replay is already running.")
            job = ReplayJob(
                job_id=f"job-{uuid.uuid4().hex[:10]}",
                request=request,
                queued_at=datetime.now(timezone.utc),
            )
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._trim_locked()
            self._running = job.job_id

        thread = threading.Thread(
            target=self._run, args=(job.job_id,), daemon=True, name="replay"
        )
        thread.start()
        return job

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
        try:
            outcome = self._runner(job.request)
        except Exception as exc:  # pragma: no cover - runner guards its own
            with self._lock:
                job.status = "failed"
                job.error = str(exc)
                self._running = None
            return
        with self._lock:
            job.outcome = outcome
            job.status = "done" if outcome.succeeded else "failed"
            job.error = outcome.error
            self._running = None

    def get(self, job_id: str) -> Optional[ReplayJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 5) -> list[ReplayJob]:
        with self._lock:
            return [self._jobs[key] for key in reversed(self._order)][:limit]

    def _trim_locked(self) -> None:
        while len(self._order) > self._max_history:
            self._jobs.pop(self._order.pop(0), None)

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._order.clear()
            self._running = None


_JOBS = ReplayJobs()


def get_replay_jobs() -> ReplayJobs:
    return _JOBS
