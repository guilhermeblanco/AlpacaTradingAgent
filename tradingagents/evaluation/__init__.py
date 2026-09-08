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

__all__ = [
    "EvaluationEpisode",
    "EvaluationHorizon",
    "EvaluationOutcome",
    "EvaluationRepository",
    "ExperimentScorecard",
    "FilledEpisodeAttributor",
    "HistoricalPriceProvider",
    "OutcomeAttributor",
    "PriceObservation",
    "PromotionDecision",
    "PromotionPolicy",
    "PromotionStatus",
    "assess_promotion",
    "calculate_outcome",
]
