"""
Command-line entrypoint.

Orchestrates one full run:
  1. (optionally) reset the seller to a fresh sale of `--tickets` tickets
  2. build the request plan (with deliberate request_id duplicates)
  3. fire the plan concurrently against the seller, timing the whole run
  4. compute throughput/latency metrics from the raw outcomes
  5. fetch GET /status from the seller
  6. independently verify all five invariants against that /status response
     and the raw per-request outcomes
  7. print a report and exit non-zero if any invariant failed

Usage:
    python -m load_test.cli --tickets 100 --requests 1000 --concurrency 100 --duplicate-rate 0.20
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time

from load_test.client import build_request_plan, fetch_status, reset_seller, run_load_test
from load_test.metrics import LoadTestMetrics, compute_metrics
from load_test.models import LoadTestConfig
from load_test.verifier import VerificationResult, verify


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ticket Stampede load tester / correctness verifier"
    )
    parser.add_argument(
        "--seller-url", default="http://localhost:8000", help="Base URL of the seller service"
    )
    parser.add_argument(
        "--tickets", type=int, default=100, help="Ticket count to reset the sale to"
    )
    parser.add_argument(
        "--requests", type=int, default=1000, help="Total number of buy requests to generate"
    )
    parser.add_argument(
        "--concurrency", type=int, default=100, help="Maximum number of in-flight requests"
    )
    parser.add_argument(
        "--duplicate-rate",
        type=float,
        default=0.0,
        help="Fraction (0.0-1.0) of requests that deliberately reuse an earlier request_id",
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="Per-request HTTP timeout in seconds"
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Skip calling /reset before the run (assumes a sale is already active)",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducible duplicate selection"
    )
    return parser.parse_args(argv)


def _print_report(
    config: LoadTestConfig,
    metrics: LoadTestMetrics,
    verification: VerificationResult,
) -> None:
    print("=" * 40)
    print("TICKET STAMPEDE LOAD TEST")
    print("=" * 40)
    print()
    print(f"Tickets: {config.ticket_total}")
    print(f"Requests: {config.num_requests}")
    print(f"Concurrency: {config.concurrency}")
    print(f"Duplicate rate: {config.duplicate_rate:.0%}")
    print()
    print(f"Requests completed: {metrics.requests_completed}/{metrics.total_requests}")
    print(f"Successful purchases: {metrics.successful_purchases}")
    print(f"Failed requests: {metrics.failed_requests}")
    print(f"Throughput: {metrics.requests_per_second:.1f} req/s")
    print(f"Median latency: {metrics.median_latency_ms:.1f} ms")
    print(f"P99 latency: {metrics.p99_latency_ms:.1f} ms")
    print(f"Total duration: {metrics.total_duration_seconds:.2f} s")
    print()
    print("INVARIANTS")
    print("-" * 40)
    name_width = max(len(c.name) for c in verification.checks) + 1
    for check in verification.checks:
        status_word = "PASS" if check.passed else "FAIL"
        print(f"{check.name + ':':<{name_width}} {status_word}")
        if not check.passed:
            print(f"    -> {check.detail}")
    print()
    print(f"Overall: {'PASS' if verification.overall_passed else 'FAIL'}")


async def _run(args: argparse.Namespace) -> int:
    config = LoadTestConfig(
        seller_url=args.seller_url,
        ticket_total=args.tickets,
        num_requests=args.requests,
        concurrency=args.concurrency,
        duplicate_rate=args.duplicate_rate,
        timeout_seconds=args.timeout,
        reset_before_run=not args.no_reset,
    )

    if config.reset_before_run:
        await reset_seller(config)

    rng = random.Random(args.seed)
    plan = build_request_plan(config, rng)

    start = time.perf_counter()
    outcomes = await run_load_test(config, plan)
    duration = time.perf_counter() - start

    metrics = compute_metrics(outcomes, duration)

    status = await fetch_status(config)
    verification = verify(plan, status, outcomes)

    _print_report(config, metrics, verification)

    return 0 if verification.overall_passed else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
