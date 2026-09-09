"""Typed validation boundary for the legacy dictionary configuration API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradingAgentsConfig(BaseModel):
    """Validate operational settings while preserving extension keys."""

    model_config = ConfigDict(extra="allow")

    project_dir: str
    results_dir: str
    data_dir: str
    data_cache_dir: str
    memory_log_path: str
    llm_provider: str
    deep_think_llm: str
    quick_think_llm: str
    max_debate_rounds: int = Field(ge=1)
    max_risk_discuss_rounds: int = Field(ge=1)
    max_recur_limit: int = Field(ge=1)
    execution_broker: Literal["alpaca", "tradier", "robinhood"]
    execution_gateway: Literal["alpaca", "broker", "dry-run"]
    research_market_data_provider: Literal["alpaca", "tradier"]
    persistence_backend: Literal["local", "postgres"]
    database_url: Optional[str] = None
    lifecycle_enabled: bool
    lifecycle_intent_ttl_seconds: int = Field(ge=1)
    max_trade_notional_usd: float = Field(ge=0)
    max_symbol_concentration_pct: float = Field(ge=0, le=100)
    daily_loss_halt_pct: float = Field(ge=0, le=100)
    max_drawdown_halt_pct: float = Field(ge=0, le=100)
    max_consecutive_rejections: int = Field(ge=0)
    daily_llm_token_budget: int = Field(ge=0)
    batch_max_workers: int = Field(ge=1)
    batch_provider_max_concurrency: int = Field(ge=1)
    batch_provider_min_interval_seconds: float = Field(ge=0)
    batch_provider_max_retries: int = Field(ge=0)
    account_reconciliation_quantity_tolerance: float = Field(ge=0)

    @field_validator(
        "execution_broker",
        "execution_gateway",
        "research_market_data_provider",
        "persistence_backend",
        mode="before",
    )
    @classmethod
    def normalize_choice(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator(
        "project_dir",
        "results_dir",
        "data_dir",
        "data_cache_dir",
        "memory_log_path",
        "llm_provider",
        "deep_think_llm",
        "quick_think_llm",
    )
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("must not be empty")
        return str(value)


def validate_application_config(
    values: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    from tradingagents.default_config import DEFAULT_CONFIG

    merged = dict(DEFAULT_CONFIG)
    merged.update(values or {})
    return TradingAgentsConfig.model_validate(merged).model_dump()
