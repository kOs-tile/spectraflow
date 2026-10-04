from benchmark.recovery_faults import run_benchmark


def test_recovery_benchmark_matches_expected_outcomes():
    result = run_benchmark()

    assert result["cases"] == 6
    assert result["exact_outcome_matches"] == 6
    assert result["exact_outcome_rate"] == 1.0
    assert result["recoverable_fault_cases"] == 3
    assert result["recovered_fault_cases"] == 3
    assert result["synthetic_recovery_rate"] == 1.0
