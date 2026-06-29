"""
CUSUM (Cumulative Sum) Control Chart Algorithm.

Implements the tabular (or algorithmic) CUSUM for detecting sustained shifts
in a univariate time series. Designed for detecting semantic drift in LLM
behavioral metrics.

Theory:
    The tabular CUSUM maintains two running sums:
      C⁺_n = max(0, C⁺_{n-1} + x_n - (μ₀ + k))   upper CUSUM (detects increase)
      C⁻_n = max(0, C⁻_{n-1} - x_n + (μ₀ - k))   lower CUSUM (detects decrease)

    A signal (alarm) is raised when C⁺_n > h or C⁻_n > h.

    Parameters:
      μ₀: target (in-control) mean — derived from baseline
      k:  reference value / allowable slack — typically 0.5 * expected_shift
      h:  decision interval / threshold — controls false positive rate

    The algorithm detects the *onset* of a sustained mean shift, not transient
    spikes. This makes it ideal for gradual semantic drift.

References:
    - Montgomery, D.C. (2009). Introduction to Statistical Quality Control.
    - Page, E.S. (1954). Continuous inspection schemes. Biometrika, 41, 100-114.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats


@dataclass
class CUSUMResult:
    """Result of a CUSUM analysis."""

    drift_detected: bool
    magnitude: float
    """Peak CUSUM statistic value (C⁺ or C⁻ at time of alarm)."""

    first_signal_index: int
    """Index in the series where CUSUM first exceeded the threshold (-1 if no signal)."""

    cusum_upper: list[float] = field(default_factory=list)
    """C⁺ series — upper CUSUM statistics."""

    cusum_lower: list[float] = field(default_factory=list)
    """C⁻ series — lower CUSUM statistics."""

    current_statistic: float = 0.0
    """Current (final) combined CUSUM statistic max(C⁺[-1], C⁻[-1])."""

    target_mean: float = 0.0
    """μ₀ — the in-control mean used for computation."""

    threshold_h: float = 5.0
    slack_k: float = 0.5

    direction: Optional[str] = None
    """'increase' | 'decrease' | None — direction of detected shift."""

    n_observations: int = 0


def run_cusum(
    observations: list[float],
    target_mean: float | None = None,
    threshold_h: float = 5.0,
    slack_k: float = 0.5,
    scale_std: float | None = None,
) -> CUSUMResult:
    """
    Run tabular CUSUM on a univariate time series.

    Args:
        observations:   Time-ordered observations (e.g., hourly centroid distances).
        target_mean:    In-control mean μ₀. If None, estimated from the first
                        quarter of the series (assumed to be in-control).
        threshold_h:    Alarm threshold. Signal when C⁺ or C⁻ > h * σ.
                        Default 5.0 is the standard recommendation.
        slack_k:        Allowable slack. Typically 0.5 * expected_shift / σ.
                        Default 0.5 means we're sensitive to ~1σ shifts.
        scale_std:      Standard deviation σ for normalizing. If None, estimated
                        from observations using median absolute deviation (robust).

    Returns:
        CUSUMResult with drift_detected, magnitude, first_signal_index.
    """
    n = len(observations)
    if n < 2:
        return CUSUMResult(
            drift_detected=False,
            magnitude=0.0,
            first_signal_index=-1,
            n_observations=n,
            threshold_h=threshold_h,
            slack_k=slack_k,
        )

    x = np.array(observations, dtype=np.float64)

    # Estimate in-control parameters
    # Use first quarter as "warm-up" period for parameter estimation
    warmup_end = max(2, n // 4)

    if target_mean is None:
        target_mean = float(np.median(x[:warmup_end]))

    if scale_std is None:
        # Robust standard deviation via MAD
        mad = float(np.median(np.abs(x[:warmup_end] - target_mean)))
        scale_std = mad * 1.4826  # MAD → Gaussian σ conversion
        if scale_std < 1e-10:
            # Near-zero variance: use range-based estimate
            scale_std = max(float(np.std(x)), 1e-6)

    # Normalize observations
    x_normalized = (x - target_mean) / scale_std

    # Tabular CUSUM
    cusum_upper = np.zeros(n, dtype=np.float64)
    cusum_lower = np.zeros(n, dtype=np.float64)

    for i in range(n):
        cusum_upper[i] = max(0.0, (cusum_upper[i - 1] if i > 0 else 0.0) + x_normalized[i] - slack_k)
        cusum_lower[i] = max(0.0, (cusum_lower[i - 1] if i > 0 else 0.0) - x_normalized[i] - slack_k)

    # Detect first alarm
    upper_alarms = np.where(cusum_upper > threshold_h)[0]
    lower_alarms = np.where(cusum_lower > threshold_h)[0]

    drift_detected = len(upper_alarms) > 0 or len(lower_alarms) > 0
    first_signal_index = -1
    direction = None

    if drift_detected:
        candidates = []
        if len(upper_alarms) > 0:
            candidates.append((upper_alarms[0], "increase"))
        if len(lower_alarms) > 0:
            candidates.append((lower_alarms[0], "decrease"))

        # Take whichever arm alarmed first
        candidates.sort(key=lambda t: t[0])
        first_signal_index, direction = candidates[0]

    # Current statistics (end of series)
    current_statistic = float(max(cusum_upper[-1], cusum_lower[-1]))
    magnitude = float(max(np.max(cusum_upper), np.max(cusum_lower)))

    return CUSUMResult(
        drift_detected=drift_detected,
        magnitude=magnitude,
        first_signal_index=int(first_signal_index),
        cusum_upper=cusum_upper.tolist(),
        cusum_lower=cusum_lower.tolist(),
        current_statistic=current_statistic,
        target_mean=target_mean,
        threshold_h=threshold_h,
        slack_k=slack_k,
        direction=direction,
        n_observations=n,
    )


def run_cusum_on_metrics(
    centroid_distances: list[float],
    variance_series: list[float] | None = None,
    threshold_h: float = 5.0,
    slack_k: float = 0.5,
) -> dict[str, CUSUMResult]:
    """
    Run CUSUM on multiple behavioral metric streams simultaneously.

    Args:
        centroid_distances:  Time series of mean cosine distances from baseline centroid.
        variance_series:     Optional time series of semantic variance values.
        threshold_h:         Alarm threshold.
        slack_k:             Allowable slack.

    Returns:
        Dictionary mapping metric name → CUSUMResult.
    """
    results: dict[str, CUSUMResult] = {}

    results["centroid_distance"] = run_cusum(
        observations=centroid_distances,
        threshold_h=threshold_h,
        slack_k=slack_k,
    )

    if variance_series is not None and len(variance_series) >= 2:
        results["semantic_variance"] = run_cusum(
            observations=variance_series,
            threshold_h=threshold_h,
            slack_k=slack_k,
        )

    return results


def estimate_drift_magnitude_sigma(
    result: CUSUMResult,
    baseline_std: float | None = None,
) -> float:
    """
    Estimate the drift magnitude in units of standard deviations.

    When CUSUM detects a shift, the shift size can be estimated as:
        δ̂ = (C⁺_T / (T - T_alarm + 1)) + k

    where T is the final time point and T_alarm is the first alarm index.

    Returns: estimated shift size in σ units.
    """
    if not result.drift_detected:
        return 0.0

    n = result.n_observations
    t_alarm = result.first_signal_index

    if t_alarm < 0 or n <= t_alarm:
        return float(result.magnitude)

    # Number of points since alarm
    n_since_alarm = max(1, n - t_alarm)

    # Estimate shift using post-alarm CUSUM statistics
    peak_stat = max(result.cusum_upper[-1], result.cusum_lower[-1])
    estimated_shift = (peak_stat / n_since_alarm) + result.slack_k

    return float(np.clip(estimated_shift, 0, 10))


def compute_cusum_summary(results: dict[str, CUSUMResult]) -> dict[str, float | bool | int | str]:
    """
    Aggregate CUSUM results across multiple metric streams into a single
    summary for incident reporting.

    Returns a flat dict suitable for JSON serialization.
    """
    any_drift = any(r.drift_detected for r in results.values())
    max_magnitude = max((r.magnitude for r in results.values()), default=0.0)
    first_signal = min(
        (r.first_signal_index for r in results.values() if r.first_signal_index >= 0),
        default=-1,
    )
    current_max = max((r.current_statistic for r in results.values()), default=0.0)

    # Determine which streams have alarmed
    alarmed_streams = [
        name for name, r in results.items() if r.drift_detected
    ]

    return {
        "drift_detected": any_drift,
        "max_magnitude": float(max_magnitude),
        "current_max_statistic": float(current_max),
        "first_signal_index": int(first_signal),
        "alarmed_streams": alarmed_streams,
        "n_streams_alarmed": len(alarmed_streams),
        "n_streams_monitored": len(results),
    }
