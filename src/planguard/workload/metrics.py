"""Metrics collected from a measured workload run."""

from __future__ import annotations

from collections.abc import Callable
from math import ceil
from statistics import mean
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field


class WorkloadResult(BaseModel):
    """Summary of one workload measurement."""

    model_config = ConfigDict(frozen=True)

    operation_count: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    throughput_ops_per_second: float = Field(ge=0)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    latency_p99_ms: float = Field(ge=0)
    latency_mean_ms: float = Field(ge=0)
    errors: int = Field(ge=0)


def _percentile(values: list[float], percentile: float) -> float:
    """Return the nearest-rank percentile from values expressed in ms."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def measure_workload(
    operation: Callable[[], None],
    *,
    operation_count: int,
) -> WorkloadResult:
    """Run an operation repeatedly and collect wall-clock latency metrics.

    Individual operation failures are counted and do not stop the run.
    The caller is responsible for ensuring the operation is safe to repeat.
    """
    if operation_count < 0:
        raise ValueError("operation_count must be non-negative")

    latencies_ms: list[float] = []
    errors = 0
    started = monotonic()

    for _ in range(operation_count):
        operation_started = monotonic()
        try:
            operation()
        except Exception:
            errors += 1
        finally:
            latencies_ms.append((monotonic() - operation_started) * 1000)

    elapsed = monotonic() - started
    successful = operation_count - errors

    return WorkloadResult(
        operation_count=successful,
        elapsed_seconds=elapsed,
        throughput_ops_per_second=successful / elapsed if elapsed > 0 else 0.0,
        latency_p50_ms=_percentile(latencies_ms, 0.50),
        latency_p95_ms=_percentile(latencies_ms, 0.95),
        latency_p99_ms=_percentile(latencies_ms, 0.99),
        latency_mean_ms=mean(latencies_ms) if latencies_ms else 0.0,
        errors=errors,
    )
