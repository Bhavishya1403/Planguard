"""Tests for workload metric collection."""

import pytest

from planguard.workload.metrics import measure_workload


def test_measures_successful_operations() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1

    result = measure_workload(operation, operation_count=10)

    assert calls == 10
    assert result.attempted_operations == 10
    assert result.successful_operations == 10
    assert result.errors == 0
    assert result.elapsed_seconds >= 0
    assert result.throughput_ops_per_second >= 0
    assert result.latency_p50_ms >= 0
    assert result.latency_p95_ms >= result.latency_p50_ms
    assert result.latency_p99_ms >= result.latency_p95_ms


def test_counts_operation_failures() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated failure")

    result = measure_workload(operation, operation_count=3)

    assert calls == 3
    assert result.attempted_operations == 3
    assert result.successful_operations == 2
    assert result.errors == 1


def test_zero_operations_returns_zero_metrics() -> None:
    result = measure_workload(lambda: None, operation_count=0)

    assert result.attempted_operations == 0
    assert result.successful_operations == 0
    assert result.errors == 0
    assert result.throughput_ops_per_second == 0
    assert result.latency_p50_ms == 0


def test_rejects_negative_operation_count() -> None:
    with pytest.raises(ValueError, match="operation_count"):
        measure_workload(lambda: None, operation_count=-1)
