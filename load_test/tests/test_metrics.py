"""
Tests for metrics computation. Pure computation over synthetic
RequestOutcome lists -- no network, no seller required.
"""
from load_test.metrics import _percentile, compute_metrics
from load_test.models import PlannedRequest, RequestOutcome


def _planned(i: int) -> PlannedRequest:
    return PlannedRequest(
        index=i,
        user_id=f"user-{i}",
        request_id=f"req-{i}",
        is_deliberate_duplicate=False,
        duplicate_of_index=None,
    )


def _outcome(i: int, status_code: int | None, latency_seconds: float, error: str | None = None):
    return RequestOutcome(
        planned=_planned(i),
        status_code=status_code,
        ticket_number=i if status_code == 200 else None,
        latency_seconds=latency_seconds,
        error=error,
    )


def test_percentile_of_empty_list_is_zero():
    assert _percentile([], 50) == 0.0


def test_percentile_of_single_value():
    assert _percentile([42.0], 50) == 42.0
    assert _percentile([42.0], 99) == 42.0


def test_percentile_median_of_known_sorted_values():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # nearest-rank p50 of 10 sorted values -> rank 5 -> value 5.0
    assert _percentile(values, 50) == 5.0


def test_percentile_p99_of_known_sorted_values():
    values = [float(i) for i in range(1, 101)]  # 1..100
    # nearest-rank p99 of 100 values -> rank 99 -> value 99.0
    assert _percentile(values, 99) == 99.0


def test_compute_metrics_counts_success_and_failure_correctly():
    outcomes = [
        _outcome(0, 200, 0.010),
        _outcome(1, 200, 0.020),
        _outcome(2, 409, 0.015),  # sold out -- a failure, but a real HTTP response
        _outcome(3, None, 5.0, error="ConnectTimeout"),  # network-level failure
    ]
    metrics = compute_metrics(outcomes, total_duration_seconds=2.0)

    assert metrics.total_requests == 4
    assert metrics.requests_completed == 3  # got a real HTTP response (200 or 409)
    assert metrics.successful_purchases == 2  # only the 200s
    assert metrics.failed_requests == 2  # the 409 and the network error
    assert metrics.requests_per_second == 2.0  # 4 requests / 2.0 seconds


def test_compute_metrics_latency_percentiles_use_all_outcomes_including_failures():
    # Latency should reflect every attempted request, not just successes --
    # a seller that only degrades on the requests that end up failing
    # would otherwise have its worst latency hidden from the report.
    outcomes = [_outcome(i, 200, latency_seconds=i / 100) for i in range(1, 101)]  # 0.01..1.00s
    metrics = compute_metrics(outcomes, total_duration_seconds=1.0)
    assert metrics.median_latency_ms == 500.0  # 50th of 1..100 (in ms) -> 500ms
    assert metrics.p99_latency_ms == 990.0


def test_compute_metrics_zero_duration_does_not_raise():
    outcomes = [_outcome(0, 200, 0.001)]
    metrics = compute_metrics(outcomes, total_duration_seconds=0.0)
    assert metrics.requests_per_second == 0.0
