"""Production entry point for autonomous portfolio cycles."""

from __future__ import annotations

import argparse
import copy
import logging
import os
import signal
from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagents.agents.schemas import TradeIntent
from tradingagents.broker.registry import default_broker_registry
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.execution import ExecutionPipeline
from tradingagents.execution.dry_run_gateway import DryRunExecutionGateway
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.persistence import build_persistence_runtime
from tradingagents.portfolio import PortfolioLimitsConfig
from tradingagents.safety import SafetyGuard
from tradingagents.screener import fetch_bulk_ohlcv, run_scan

from .autonomous import AutonomousCycleScheduler
from .batch import BatchOrchestrator, Candidate, ProviderPolicy
from .execution_coordinator import ReservationAwareExecutionCoordinator


LOGGER = logging.getLogger(__name__)


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def build_scheduler_from_env():
    if not _enabled("AUTONOMOUS_ENABLED"):
        raise ValueError("Set AUTONOMOUS_ENABLED=true to start autonomous analysis")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(
        {
            "execution_broker": os.getenv("EXECUTION_BROKER", "alpaca"),
            "execution_gateway": os.getenv("EXECUTION_GATEWAY", "dry-run"),
            "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "postgres"),
            "database_url": os.getenv("DATABASE_URL"),
            "llm_provider": os.getenv("LLM_PROVIDER", config["llm_provider"]),
            "deep_think_llm": os.getenv("DEEP_THINK_LLM", config["deep_think_llm"]),
            "quick_think_llm": os.getenv("QUICK_THINK_LLM", config["quick_think_llm"]),
        }
    )
    persistence = build_persistence_runtime(config)
    if persistence.unit_of_work_factory is None:
        persistence.close()
        raise ValueError("autonomous scheduler requires PERSISTENCE_BACKEND=postgres")

    broker = default_broker_registry().create(config["execution_broker"], config)
    gateway = broker.execution_gateway
    if config["execution_gateway"].lower() == "dry-run":
        gateway = DryRunExecutionGateway()
    pipeline = ExecutionPipeline(
        broker.snapshot_provider,
        gateway,
        safety_guard=SafetyGuard(config),
        unit_of_work_factory=persistence.unit_of_work_factory,
        lifecycle_enabled=True,
        lifecycle_ttl_seconds=config["lifecycle_intent_ttl_seconds"],
    )
    coordinator = ReservationAwareExecutionCoordinator(
        persistence.unit_of_work_factory,
        pipeline.execute,
        claim_lease_seconds=int(os.getenv("AUTONOMOUS_DISPATCH_LEASE_SECONDS", "60")),
    )
    analysts = [
        value.strip()
        for value in os.getenv(
            "AUTONOMOUS_ANALYSTS", "market,social,news,fundamentals,macro"
        ).split(",")
        if value.strip()
    ]

    def candidate_source(snapshot):
        scan = run_scan(
            asset_filter=os.getenv("AUTONOMOUS_ASSET_FILTER", "all"),
            owned_symbols={position.symbol for position in snapshot.positions},
        )
        return [
            Candidate(
                symbol=row["symbol"],
                asset_class=row.get("asset_type", "stock"),
                score=float(row["score"]),
                source="screener",
                provenance={
                    "price": row.get("price"),
                    "signals": row.get("signals", {}),
                    "scan_time": scan.get("scan_time"),
                },
            )
            for row in scan.get("candidates", [])
        ]

    def analyze(candidate: Candidate) -> TradeIntent:
        graph = TradingAgentsGraph(analysts, config=config, debug=False)
        trade_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        state, _ = graph.propagate(candidate.symbol, trade_date)
        return TradeIntent.model_validate(state["final_trade_intent"])

    allowed = {"equity"}
    if broker.capabilities.crypto:
        allowed.add("crypto")
    orchestrator = BatchOrchestrator(
        max_workers=int(os.getenv("AUTONOMOUS_MAX_CONCURRENCY", "2")),
        provider_policies={
            config["llm_provider"]: ProviderPolicy(
                max_concurrency=int(os.getenv("AUTONOMOUS_PROVIDER_CONCURRENCY", "2")),
                min_interval_seconds=float(
                    os.getenv("AUTONOMOUS_PROVIDER_MIN_INTERVAL_SECONDS", "0.25")
                ),
                max_retries=int(os.getenv("AUTONOMOUS_ANALYSIS_RETRIES", "1")),
            )
        },
    )
    scheduler = AutonomousCycleScheduler(
        snapshot_provider=broker.snapshot_provider,
        candidate_source=candidate_source,
        analysis_handler=analyze,
        price_history_loader=fetch_bulk_ohlcv,
        requested_notional=lambda candidate, intent: float(
            os.getenv("AUTONOMOUS_REQUESTED_NOTIONAL_USD", "1000")
        ),
        execution_coordinator=coordinator,
        unit_of_work_factory=persistence.unit_of_work_factory,
        account_key=os.getenv("AUTONOMOUS_ACCOUNT_KEY", ""),
        analysis_provider=config["llm_provider"],
        allowed_asset_classes=allowed,
        limits=PortfolioLimitsConfig.from_config(config),
        max_symbol_concentration_pct=float(config["max_symbol_concentration_pct"]),
        max_candidates=int(os.getenv("AUTONOMOUS_MAX_CANDIDATES", "3")),
        orchestrator=orchestrator,
        estimated_tokens_per_analysis=int(
            os.getenv("AUTONOMOUS_ESTIMATED_TOKENS_PER_ANALYSIS", "0")
        ),
        reservation_ttl_seconds=int(
            os.getenv("AUTONOMOUS_RESERVATION_TTL_SECONDS", "300")
        ),
    )
    return scheduler, persistence.close


def main() -> None:
    parser = argparse.ArgumentParser(description="Run autonomous trading cycles")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("AUTONOMOUS_INTERVAL_SECONDS", "1800")),
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    scheduler, close = build_scheduler_from_env()
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: scheduler.request_stop())
        if args.once:
            result = scheduler.run_once()
            LOGGER.info("autonomous cycle result=%s", result)
        else:
            scheduler.run_forever(interval_seconds=args.interval_seconds)
    finally:
        close()


if __name__ == "__main__":
    main()
