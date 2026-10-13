"""Pydantic data contracts shared by analysis, experiments, and reports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["INFO", "WARN", "CRITICAL"]
VerdictName = Literal["PASS", "WARN", "FAIL"]


class Finding(BaseModel):
    """One actionable static or measured risk with a source location."""

    model_config = ConfigDict(extra="forbid")
    rule_id: str
    severity: Severity
    statement: str
    line: int = Field(ge=1)
    lock_mode: str
    blocks: Literal["reads", "writes", "both", "none"]
    explanation: str
    rewrite: str | None = None


class Measurement(BaseModel):
    """One experiment's client-side timing and sampled lock evidence."""

    model_config = ConfigDict(extra="forbid")
    rows: int = Field(gt=0)
    trial: int = Field(ge=0)
    write_stall_seconds: float = Field(ge=0)
    max_write_latency_seconds: float = Field(ge=0)
    baseline_p50_seconds: float = Field(ge=0)
    baseline_p95_seconds: float = Field(ge=0)
    baseline_p99_seconds: float = Field(ge=0)
    migration_p50_seconds: float = Field(ge=0)
    migration_p95_seconds: float = Field(ge=0)
    migration_p99_seconds: float = Field(ge=0)
    write_throughput_per_second: float = Field(ge=0)
    errors: int = Field(default=0, ge=0)
    timeouts: int = Field(default=0, ge=0)
    migration_wall_seconds: float = Field(ge=0)
    peak_waiting_backends: int = Field(default=0, ge=0)
    longest_lock_wait_seconds: float = Field(default=0, ge=0)
    common_blocker: str | None = None


class PlanDiff(BaseModel):
    """Classification of one query plan change."""

    model_config = ConfigDict(extra="forbid")
    query_name: str
    classification: Literal[
        "REGRESSION_SCAN", "REGRESSION_COST", "IMPROVEMENT", "UNCHANGED", "UNUSED_NEW_INDEX"
    ]
    before_cost: float = Field(ge=0)
    after_cost: float = Field(ge=0)
    cost_ratio: float = Field(ge=0)
    details: str


class Verdict(BaseModel):
    """Final safety classification and human-readable evidence."""

    model_config = ConfigDict(extra="forbid")
    status: VerdictName
    evidence: list[str] = Field(default_factory=list)


class EnvironmentInfo(BaseModel):
    """Machine metadata attached to all generated benchmark results."""

    postgres_version: str = "unavailable"
    cpu_model: str = "unavailable"
    cpu_cores: int = 0
    ram: str = "unavailable"
    storage_type: str = "unavailable"
    os: str = "unavailable"
    container_runtime: str = "external server"
    pgdata_tmpfs: bool = False


class RunResult(BaseModel):
    """Serializable output of a full PlanGuard run."""

    model_config = ConfigDict(extra="forbid")
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
    plan_diffs: list[PlanDiff] = Field(default_factory=list)
    environment: EnvironmentInfo = Field(default_factory=EnvironmentInfo)
    assumptions: list[str] = Field(default_factory=list)
