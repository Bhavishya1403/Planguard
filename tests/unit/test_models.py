"""Tests for phase 0 shared result models and their validation boundaries."""

import pytest
from pydantic import ValidationError

from planguard.models import (
    EnvironmentInfo,
    Finding,
    Measurement,
    PlanDiff,
    RunResult,
    Verdict,
)


def test_finding_records_location_and_risk_details() -> None:
    """Findings should preserve the information reports need to show evidence."""
    finding = Finding(
        rule_id="PG001",
        severity="WARN",
        statement="CREATE INDEX ix_orders_created ON orders (created_at)",
        line=4,
        lock_mode="SHARE",
        blocks="writes",
        explanation="A standard index build blocks writes.",
    )

    assert finding.line == 4
    assert finding.rewrite is None


def test_finding_requires_a_positive_line_number() -> None:
    """Source locations must use one-based line numbering."""
    with pytest.raises(ValidationError):
        Finding(
            rule_id="PG001",
            severity="WARN",
            statement="CREATE INDEX ix_orders_created ON orders (created_at)",
            line=0,
            lock_mode="SHARE",
            blocks="writes",
            explanation="A standard index build blocks writes.",
        )


def test_measurement_requires_positive_size_and_nonnegative_metrics() -> None:
    """Measurement values should not silently accept impossible quantities."""
    with pytest.raises(ValidationError):
        Measurement(
            rows=0,
            trial=0,
            write_stall_seconds=0,
            max_write_latency_seconds=0,
            baseline_p50_seconds=0,
            baseline_p95_seconds=0,
            baseline_p99_seconds=0,
            migration_p50_seconds=0,
            migration_p95_seconds=0,
            migration_p99_seconds=0,
            write_throughput_per_second=0,
            migration_wall_seconds=0,
        )


def test_run_result_serializes_nested_models() -> None:
    """A run result should produce a fully serializable report payload."""
    result = RunResult(
        verdict=Verdict(status="PASS", evidence=["No configured threshold was exceeded."]),
        environment=EnvironmentInfo(postgres_version="16.4", cpu_cores=8),
    )

    serialized = result.model_dump(mode="json")

    assert serialized["verdict"]["status"] == "PASS"
    assert serialized["environment"]["postgres_version"] == "16.4"
    assert serialized["measurements"] == []


def test_plan_diff_classification_is_constrained() -> None:
    """Only documented plan regression classifications may enter reports."""
    with pytest.raises(ValidationError):
        PlanDiff(
            query_name="recent-orders",
            classification="BAD_CLASSIFICATION",
            before_cost=10,
            after_cost=5,
            cost_ratio=0.5,
            details="Invalid test classification.",
        )
