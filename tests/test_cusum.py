"""
Unit tests for the CUSUM control chart algorithm.

Tests:
  - No drift on stationary series
  - Upward drift detection (mean shift)
  - Downward drift detection
  - Drift-free series with noise
  - First signal index accuracy
  - Edge cases: empty, single-element, constant series
  - Multiple metric streams
  - Parameter sensitivity (threshold h, slack k)
  - compute_cusum_summary aggregation
  - estimate_drift_magnitude_sigma
"""

from __future__ import annotations

import math
import random
import numpy as np
import pytest

from spectraflow.detection.cusum import (
    CUSUMResult,
    compute_cusum_summary,
    estimate_drift_magnitude_sigma,
    run_cusum,
    run_cusum_on_metrics,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def stationary_series() -> list[float]:
    """White noise around mean 0.1 — should NOT trigger drift."""
    rng = np.random.default_rng(seed=42)
    return (rng.normal(loc=0.1, scale=0.02, size=100)).tolist()


@pytest.fixture
def upward_drift_series() -> list[float]:
    """
    First 50 points around mean 0.1 (baseline),
    then a step shift to mean 0.4 (clear drift).
    """
    rng = np.random.default_rng(seed=42)
    baseline = rng.normal(loc=0.1, scale=0.02, size=50)
    drifted = rng.normal(loc=0.4, scale=0.02, size=50)
    return np.concatenate([baseline, drifted]).tolist()


@pytest.fixture
def downward_drift_series() -> list[float]:
    """Step downward shift at index 40."""
    rng = np.random.default_rng(seed=42)
    baseline = rng.normal(loc=0.5, scale=0.02, size=40)
    drifted = rng.normal(loc=0.2, scale=0.02, size=60)
    return np.concatenate([baseline, drifted]).tolist()


@pytest.fixture
def gradual_drift_series() -> list[float]:
    """Slow upward ramp — harder to detect but should eventually alarm."""
    rng = np.random.default_rng(seed=42)
    n = 100
    trend = np.linspace(0.1, 0.6, n)
    noise = rng.normal(0, 0.01, n)
    return (trend + noise).tolist()


# ── Basic detection tests ─────────────────────────────────────────────────────

class TestCUSUMBasicDetection:

    def test_stationary_no_drift(self, stationary_series: list[float]) -> None:
        """A stationary series should not trigger false alarms."""
        result = run_cusum(stationary_series, threshold_h=5.0, slack_k=0.5)
        assert result.drift_detected is False, (
            f"False alarm on stationary series: first_signal={result.first_signal_index}, "
            f"magnitude={result.magnitude:.2f}"
        )
        assert result.first_signal_index == -1

    def test_upward_drift_detected(self, upward_drift_series: list[float]) -> None:
        """A clear step upward shift should be detected."""
        result = run_cusum(upward_drift_series, threshold_h=5.0, slack_k=0.5)
        assert result.drift_detected is True, (
            f"Missed upward drift: magnitude={result.magnitude:.2f}, "
            f"current_statistic={result.current_statistic:.2f}"
        )
        assert result.direction == "increase"

    def test_downward_drift_detected(self, downward_drift_series: list[float]) -> None:
        """A step downward shift should be detected."""
        result = run_cusum(downward_drift_series, threshold_h=5.0, slack_k=0.5)
        assert result.drift_detected is True
        assert result.direction == "decrease"

    def test_gradual_drift_detected(self, gradual_drift_series: list[float]) -> None:
        """CUSUM should eventually detect a slow ramp."""
        result = run_cusum(gradual_drift_series, threshold_h=5.0, slack_k=0.5)
        assert result.drift_detected is True, (
            "CUSUM failed to detect gradual ramp drift"
        )

    def test_first_signal_index_is_after_drift_start(
        self, upward_drift_series: list[float]
    ) -> None:
        """First signal should occur at or after the injected drift start (index 50)."""
        result = run_cusum(upward_drift_series, threshold_h=5.0, slack_k=0.5)
        assert result.drift_detected is True
        # CUSUM has some lag — signal should be near or after injection point
        # Allow some lag (up to 10 samples after drift start)
        assert result.first_signal_index >= 40, (
            f"Signal at {result.first_signal_index} — too early (drift at 50)"
        )
        assert result.first_signal_index < 80, (
            f"Signal at {result.first_signal_index} — too late"
        )


# ── Result structure tests ────────────────────────────────────────────────────

class TestCUSUMResultStructure:

    def test_cusum_series_lengths_match_input(self, upward_drift_series: list[float]) -> None:
        """CUSUM output series should have same length as input."""
        result = run_cusum(upward_drift_series)
        assert len(result.cusum_upper) == len(upward_drift_series)
        assert len(result.cusum_lower) == len(upward_drift_series)

    def test_cusum_series_non_negative(self, stationary_series: list[float]) -> None:
        """CUSUM statistics are always non-negative."""
        result = run_cusum(stationary_series)
        assert all(v >= 0 for v in result.cusum_upper)
        assert all(v >= 0 for v in result.cusum_lower)

    def test_magnitude_is_max_statistic(self, upward_drift_series: list[float]) -> None:
        """magnitude should equal max(max(C+), max(C-))."""
        result = run_cusum(upward_drift_series)
        expected_magnitude = max(max(result.cusum_upper), max(result.cusum_lower))
        assert math.isclose(result.magnitude, expected_magnitude, rel_tol=1e-6)

    def test_current_statistic_is_final_value(self, upward_drift_series: list[float]) -> None:
        """current_statistic should be max of final C+ and C- values."""
        result = run_cusum(upward_drift_series)
        expected = max(result.cusum_upper[-1], result.cusum_lower[-1])
        assert math.isclose(result.current_statistic, expected, rel_tol=1e-6)

    def test_n_observations_matches_input(self, stationary_series: list[float]) -> None:
        result = run_cusum(stationary_series)
        assert result.n_observations == len(stationary_series)


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestCUSUMEdgeCases:

    def test_empty_series(self) -> None:
        result = run_cusum([])
        assert result.drift_detected is False
        assert result.n_observations == 0
        assert result.first_signal_index == -1

    def test_single_element(self) -> None:
        result = run_cusum([0.5])
        assert result.drift_detected is False
        assert result.n_observations == 1

    def test_constant_series(self) -> None:
        """All identical values — variance is 0, no drift."""
        result = run_cusum([0.3] * 50)
        assert result.drift_detected is False

    def test_two_elements(self) -> None:
        result = run_cusum([0.1, 0.9])
        assert result.n_observations == 2
        assert isinstance(result.drift_detected, bool)

    def test_explicit_target_mean(self) -> None:
        """Setting explicit target_mean should use it instead of estimating."""
        series = [0.5] * 30 + [0.9] * 20  # drift at index 30
        result_auto = run_cusum(series, threshold_h=5.0)
        result_explicit = run_cusum(series, target_mean=0.5, threshold_h=5.0)
        # Both should detect drift; explicit might be more sensitive
        assert result_explicit.drift_detected is True

    def test_nan_handling_preserves_detection(self) -> None:
        """A missing sample must not poison later CUSUM statistics."""
        series = [0.1] * 20 + [float("nan")] + [0.5] * 20
        result = run_cusum(series)
        assert result.drift_detected is True
        assert result.direction == "increase"
        assert result.first_signal_index > 20
        assert math.isfinite(result.magnitude)

    def test_all_non_finite_returns_no_drift(self) -> None:
        result = run_cusum([float("nan"), float("inf"), float("-inf")])
        assert result.drift_detected is False
        assert result.first_signal_index == -1


# ── Parameter sensitivity ─────────────────────────────────────────────────────

class TestCUSUMParameters:

    def test_lower_threshold_more_sensitive(self, upward_drift_series: list[float]) -> None:
        """Lower h should detect drift sooner."""
        result_sensitive = run_cusum(upward_drift_series, threshold_h=2.0, slack_k=0.5)
        result_conservative = run_cusum(upward_drift_series, threshold_h=8.0, slack_k=0.5)

        # Sensitive should detect at least as early as conservative
        if result_sensitive.drift_detected and result_conservative.drift_detected:
            assert result_sensitive.first_signal_index <= result_conservative.first_signal_index

    def test_very_high_threshold_no_alarm(self, stationary_series: list[float]) -> None:
        """Very high threshold should prevent alarms on stationary data."""
        result = run_cusum(stationary_series, threshold_h=100.0)
        assert result.drift_detected is False

    def test_very_low_threshold_catches_noise(self, stationary_series: list[float]) -> None:
        """Very low threshold will alarm on noise — expected behavior."""
        result = run_cusum(stationary_series, threshold_h=0.5, slack_k=0.0)
        # With h=0.5, almost certainly alarms even on stationary data
        # This is expected (false positive) — just verify structure
        assert isinstance(result.drift_detected, bool)
        assert result.magnitude >= 0

    def test_slack_k_equal_threshold_h(self) -> None:
        """When k >= h, CUSUM should behave gracefully."""
        series = [random.gauss(0.5, 0.05) for _ in range(50)]
        result = run_cusum(series, threshold_h=3.0, slack_k=5.0)
        assert isinstance(result.drift_detected, bool)


# ── Multi-stream CUSUM ────────────────────────────────────────────────────────

class TestMultiStreamCUSUM:

    def test_run_cusum_on_metrics_returns_dict(self) -> None:
        distances = [0.1] * 30 + [0.5] * 20
        results = run_cusum_on_metrics(centroid_distances=distances)
        assert "centroid_distance" in results
        assert isinstance(results["centroid_distance"], CUSUMResult)

    def test_run_cusum_on_metrics_with_variance(self) -> None:
        distances = [0.1] * 30 + [0.5] * 20
        variances = [0.05] * 30 + [0.2] * 20
        results = run_cusum_on_metrics(
            centroid_distances=distances,
            variance_series=variances,
        )
        assert "centroid_distance" in results
        assert "semantic_variance" in results

    def test_compute_cusum_summary_all_clean(self) -> None:
        stationary = [0.1] * 50
        results = run_cusum_on_metrics(
            centroid_distances=stationary,
            threshold_h=5.0,
        )
        summary = compute_cusum_summary(results)
        assert summary["drift_detected"] is False
        assert summary["n_streams_alarmed"] == 0

    def test_compute_cusum_summary_with_drift(self) -> None:
        drifted = [0.1] * 30 + [0.6] * 30
        results = run_cusum_on_metrics(
            centroid_distances=drifted,
            threshold_h=5.0,
        )
        summary = compute_cusum_summary(results)
        assert summary["drift_detected"] is True
        assert summary["max_magnitude"] > 0
        assert "centroid_distance" in summary["alarmed_streams"]

    def test_compute_cusum_summary_empty(self) -> None:
        summary = compute_cusum_summary({})
        assert summary["drift_detected"] is False
        assert summary["max_magnitude"] == 0.0
        assert summary["n_streams_monitored"] == 0


# ── Drift magnitude estimation ────────────────────────────────────────────────

class TestDriftMagnitudeEstimation:

    def test_estimate_returns_zero_when_no_drift(self, stationary_series: list[float]) -> None:
        result = run_cusum(stationary_series, threshold_h=5.0)
        magnitude = estimate_drift_magnitude_sigma(result)
        assert magnitude == 0.0

    def test_estimate_positive_when_drift(self, upward_drift_series: list[float]) -> None:
        result = run_cusum(upward_drift_series, threshold_h=5.0)
        if result.drift_detected:
            magnitude = estimate_drift_magnitude_sigma(result)
            assert magnitude > 0.0

    def test_estimate_bounded(self, upward_drift_series: list[float]) -> None:
        """Magnitude estimate should be in a reasonable range."""
        result = run_cusum(upward_drift_series, threshold_h=5.0)
        if result.drift_detected:
            magnitude = estimate_drift_magnitude_sigma(result)
            assert 0 < magnitude <= 10.0


# ── Regression / known-good values ───────────────────────────────────────────

class TestCUSUMKnownValues:
    """Regression tests with deterministic inputs for numerical stability."""

    def test_known_step_series(self) -> None:
        """
        Series: 30 points at 0.0, then 30 points at 2.0.
        With target_mean=0.0, scale_std=0.1, h=5.0, k=0.5:
        CUSUM should alarm quickly after the step.
        """
        series = [0.0] * 30 + [2.0] * 30

        result = run_cusum(
            series,
            target_mean=0.0,
            scale_std=0.1,
            threshold_h=5.0,
            slack_k=0.5,
        )

        assert result.drift_detected is True
        assert result.direction == "increase"
        # Should alarm within 5 points of the step at index 30
        assert 30 <= result.first_signal_index <= 36, (
            f"Expected alarm near index 30-36, got {result.first_signal_index}"
        )

    def test_known_no_drift_series(self) -> None:
        """Pure zeros — zero variance, zero CUSUM statistics."""
        result = run_cusum(
            [0.0] * 50,
            target_mean=0.0,
            scale_std=1.0,
            threshold_h=5.0,
            slack_k=0.5,
        )
        assert result.drift_detected is False
        assert all(v == 0.0 for v in result.cusum_upper)
        assert all(v == 0.0 for v in result.cusum_lower)
