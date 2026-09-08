"""Point-in-time evaluation for autonomous trading decisions."""

from .models import EvaluationEpisode, EvaluationOutcome
from .outcomes import calculate_outcome
from .repository import EvaluationRepository
from .promotion import (
    ExperimentScorecard,
    PromotionDecision,
    PromotionPolicy,
    PromotionStatus,
    assess_promotion,
)
from .attribution import (
    EvaluationHorizon,
    FilledEpisodeAttributor,
    HistoricalPriceProvider,
    OutcomeAttributor,
    PriceObservation,
)
from .alpaca_prices import (
    AlpacaHistoricalPriceProvider,
    PriceObservationUnavailable,
)
from .price_registry import (
    HistoricalPriceProviderRegistry,
    default_historical_price_registry,
)

__all__ = [
    "AlpacaHistoricalPriceProvider",
    "EvaluationEpisode",
    "EvaluationHorizon",
    "EvaluationOutcome",
    "EvaluationRepository",
    "ExperimentScorecard",
    "FilledEpisodeAttributor",
    "HistoricalPriceProvider",
    "HistoricalPriceProviderRegistry",
    "OutcomeAttributor",
    "PriceObservation",
    "PriceObservationUnavailable",
    "PromotionDecision",
    "PromotionPolicy",
    "PromotionStatus",
    "assess_promotion",
    "calculate_outcome",
    "default_historical_price_registry",
]
