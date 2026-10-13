"""Tests for phase 0 configuration validation and YAML loading."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from planguard.config import PlanGuardConfig, ThresholdConfig, WorkloadConfig


def test_default_configuration_has_expected_values() -> None:
    """Defaults should be usable for the later experiment runner."""
    config = PlanGuardConfig()

    assert config.postgres_version == "16"
    assert config.sizes == [100_000, 500_000, 1_000_000]
    assert config.workload.writers == 2
    assert config.thresholds.fail_stall_seconds >= config.thresholds.warn_stall_seconds


def test_sizes_must_be_positive_and_nonempty() -> None:
    """Reject empty sweeps and row counts that cannot describe a table."""
    with pytest.raises(ValidationError):
        PlanGuardConfig(sizes=[])

    with pytest.raises(ValidationError):
        PlanGuardConfig(sizes=[100, 0])


def test_operation_weights_must_sum_to_one() -> None:
    """A workload profile must define a coherent operation distribution."""
    with pytest.raises(ValidationError, match="must sum to 1"):
        WorkloadConfig(insert_weight=0.4, update_weight=0.4, delete_weight=0.4)


def test_fail_stall_threshold_cannot_be_lower_than_warn_threshold() -> None:
    """Threshold order should not make a WARN more severe than a FAIL."""
    with pytest.raises(ValidationError, match="greater than or equal"):
        ThresholdConfig(warn_stall_seconds=2.0, fail_stall_seconds=1.0)


def test_yaml_loader_reads_a_valid_mapping(tmp_path: Path) -> None:
    """Load YAML from disk and validate it through the same model as Python input."""
    config_path = tmp_path / "planguard.yaml"
    config_path.write_text("postgres_version: '16'\nsizes: [25, 50]\n", encoding="utf-8")

    config = PlanGuardConfig.from_yaml(config_path)

    assert config.sizes == [25, 50]


def test_yaml_loader_rejects_non_mapping_documents(tmp_path: Path) -> None:
    """Refuse YAML scalars and lists before Pydantic sees them."""
    config_path = tmp_path / "planguard.yaml"
    config_path.write_text("- 1\n- 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML mapping"):
        PlanGuardConfig.from_yaml(config_path)
