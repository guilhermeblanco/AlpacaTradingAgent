"""Typed, validated autonomous options execution."""

from .gateway import AlpacaOptionsGateway, OptionsExecutionGateway
from .models import OptionLeg, OptionsExecutionResult, OptionsTradeIntent
from .pipeline import OptionsExecutionPipeline, execute_autonomous_options_trade
from .validator import OptionsRiskPolicy, deterministic_max_loss_usd, validate_options_intent

__all__ = [
    "AlpacaOptionsGateway", "OptionLeg", "OptionsExecutionGateway",
    "OptionsExecutionPipeline", "OptionsExecutionResult", "OptionsRiskPolicy",
    "OptionsTradeIntent", "deterministic_max_loss_usd", "execute_autonomous_options_trade",
    "validate_options_intent",
]
