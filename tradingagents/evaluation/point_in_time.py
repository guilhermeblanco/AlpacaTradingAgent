from __future__ import annotations

from datetime import datetime, timezone

from .models import EvaluationEpisode


class PointInTimeViolation(ValueError):
    pass


def ensure_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PointInTimeViolation(f"{field} must include a timezone")


def validate_episode_point_in_time(episode: EvaluationEpisode) -> None:
    ensure_aware(episode.decision_at, "decision_at")
    ensure_aware(episode.data_as_of, "data_as_of")
    if episode.data_as_of.astimezone(timezone.utc) > episode.decision_at.astimezone(timezone.utc):
        raise PointInTimeViolation("data_as_of cannot be after decision_at")


def validate_outcome_time(episode: EvaluationEpisode, outcome_at: datetime) -> None:
    ensure_aware(outcome_at, "outcome_at")
    if outcome_at.astimezone(timezone.utc) <= episode.decision_at.astimezone(timezone.utc):
        raise PointInTimeViolation("outcome_at must be after decision_at")
