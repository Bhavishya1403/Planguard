"""Validated configuration for repeatable experiments and safe local execution."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkloadConfig(BaseModel):
    """Controls independent reader and writer workers."""

    model_config = ConfigDict(extra="forbid")
    writers: int = Field(default=2, ge=0)
    readers: int = Field(default=2, ge=0)
    insert_weight: float = Field(default=0.2, ge=0)
    update_weight: float = Field(default=0.6, ge=0)
    delete_weight: float = Field(default=0.2, ge=0)
    think_time_seconds: float = Field(default=0.005, ge=0)
    target_table: str = "orders"
    statement_timeout_ms: int = Field(default=5000, gt=0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> Self:
        """Reject workload profiles whose operation shares are ambiguous."""
        total = self.insert_weight + self.update_weight + self.delete_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError("insert_weight, update_weight, and delete_weight must sum to 1")
        return self


class ThresholdConfig(BaseModel):
    """Verdict thresholds; ratios are dimensionless."""

    model_config = ConfigDict(extra="forbid")
    warn_stall_seconds: float = Field(default=0.5, ge=0)
    fail_stall_seconds: float = Field(default=2.0, ge=0)
    p99_ratio: float = Field(default=3.0, gt=0)
    plan_cost_ratio: float = Field(default=2.0, gt=0)
    fail_on_warn: bool = False

    @model_validator(mode="after")
    def fail_threshold_follows_warn(self) -> Self:
        """Keep the severity ordering meaningful for all configured thresholds."""
        if self.fail_stall_seconds < self.warn_stall_seconds:
            raise ValueError(
                "fail_stall_seconds must be greater than or equal to warn_stall_seconds"
            )
        return self


class PlanGuardConfig(BaseModel):
    """Complete run configuration with conservative bounded defaults."""

    model_config = ConfigDict(extra="forbid")
    postgres_version: str = "16"
    postgres_image: str = "postgres:16"
    runtime: Literal["auto", "podman", "docker"] = "auto"
    sizes: list[int] = Field(default_factory=lambda: [100_000, 500_000, 1_000_000])
    trials: int = Field(default=3, ge=1)
    warmup_seconds: float = Field(default=5, ge=0)
    baseline_seconds: float = Field(default=15, gt=0)
    migration_seconds: float = Field(default=60, gt=0)
    cooldown_seconds: float = Field(default=15, ge=0)
    lock_timeout: str | None = None
    max_run_seconds: int = Field(default=900, gt=0)
    pgdata_tmpfs: bool = False
    workload: WorkloadConfig = Field(default_factory=WorkloadConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)

    @field_validator("sizes")
    @classmethod
    def positive_sizes(cls, sizes: list[int]) -> list[int]:
        if not sizes or any(size <= 0 for size in sizes):
            raise ValueError("sizes must contain positive row counts")
        return sizes

    @classmethod
    def from_yaml(cls, path: Path) -> PlanGuardConfig:
        """Load a YAML mapping and validate it before any infrastructure starts."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("configuration file must contain a YAML mapping")
        return cls.model_validate(raw)
