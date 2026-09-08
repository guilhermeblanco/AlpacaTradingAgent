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
    EntryFill,
    EvaluationHorizon,
    FilledEpisodeAttributor,
    HistoricalPriceProvider,
    OutcomeAttributor,
    PriceObservation,
    attribute_episode_outcome,
    build_filled_episode,
    build_signal_episode,
)
from .experiments import (
    DeterministicExperimentAssigner,
    ExperimentAssignment,
    ExperimentVariant,
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
    "EntryFill",
    "EvaluationHorizon",
    "EvaluationOutcome",
    "DeterministicExperimentAssigner",
    "EvaluationRepository",
    "ExperimentScorecard",
    "ExperimentAssignment",
    "ExperimentVariant",
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
    "attribute_episode_outcome",
    "build_filled_episode",
    "build_signal_episode",
    "calculate_outcome",
    "default_historical_price_registry",
]
