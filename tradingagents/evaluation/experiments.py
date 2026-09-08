"""Stable experiment cohort assignment for autonomous decisions."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field


class ExperimentVariant(BaseModel):
    experiment_id: str = Field(min_length=1)
    weight: int = Field(default=1, ge=1)
    execution_eligible: bool = False
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class ExperimentAssignment(BaseModel):
    experiment_id: str
    execution_eligible: bool
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class DeterministicExperimentAssigner:
    def __init__(self, variants: list[ExperimentVariant], *, seed: str = "default"):
        if not variants:
            raise ValueError("at least one experiment variant is required")
        names = [variant.experiment_id for variant in variants]
        if len(names) != len(set(names)):
            raise ValueError("experiment IDs must be unique")
        if sum(variant.execution_eligible for variant in variants) > 1:
            raise ValueError("at most one experiment variant may execute")
        self.variants = tuple(variants)
        self.seed = seed
        self.total_weight = sum(variant.weight for variant in variants)

    def assign(self, unit_key: str) -> ExperimentAssignment:
        if not unit_key.strip():
            raise ValueError("experiment assignment unit key is required")
        digest = hashlib.sha256(f"{self.seed}:{unit_key}".encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % self.total_weight
        cursor = 0
        for variant in self.variants:
            cursor += variant.weight
            if bucket < cursor:
                return ExperimentAssignment(
                    experiment_id=variant.experiment_id,
                    execution_eligible=variant.execution_eligible,
                    config_overrides=variant.config_overrides,
                )
        raise AssertionError("unreachable")
