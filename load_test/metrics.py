"""
Metrics computed from the raw list of RequestOutcomes. Nothing here talks
to the network or the seller -- pure computation over data already
collected, so it's straightforward to unit test in isolation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from load_test.models import RequestOutcome


@dataclass(frozen=True)
class LoadTestMetrics:
    total_requests: int
    requests_completed: int  # got any HTTP response, success or error status
    successful_purchases: int  # HTTP 200 specifically
    failed_requests: int  # non-200 HTTP response OR network-level error
    requests_per_second: float
    median_latency_ms: float
    p99_latency_ms: float
    total_duration_seconds: float


def _percentile(sorted_values: list[float], pct: float) -> float:
    """
    Nearest-rank percentile over already-sorted values. pct is 0-100.

    We use nearest-rank (not linear interpolation) because it's simple,
    has no edge-case surprises on small samples, and is precise enough
    for reporting p50/p99 on request counts in the hundreds-to-thousands
    range this tool is meant for. It is a deliberate simplification --
    see the "limitations" note in the CLI's explanation output.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = math.ceil((pct / 100) * len(sorted_values))
    rank = max(1, min(rank, len(sorted_values)))
    return sorted_values[rank - 1]


def compute_metrics(
    outcomes: list[RequestOutcome], total_duration_seconds: float
) -> LoadTestMetrics:
    total_requests = len(outcomes)
    # "Completed" = we got a real HTTP response at all, success or failure status.
    completed = [o for o in outcomes if o.status_code is not None]
    successful = [o for o in completed if o.status_code == 200]
    failed_count = total_requests - len(successful)

    latencies_ms = sorted(o.latency_seconds * 1000 for o in outcomes)

    rps = total_requests / total_duration_seconds if total_duration_seconds > 0 else 0.0

    return LoadTestMetrics(
        total_requests=total_requests,
        requests_completed=len(completed),
        successful_purchases=len(successful),
        failed_requests=failed_count,
        requests_per_second=rps,
        median_latency_ms=_percentile(latencies_ms, 50),
        p99_latency_ms=_percentile(latencies_ms, 99),
        total_duration_seconds=total_duration_seconds,
    )
