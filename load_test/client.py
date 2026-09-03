"""
The client: builds the request plan, then fires it concurrently against
the seller.
 
Concurrency control
--------------------
We use an asyncio.Semaphore sized to `concurrency` to bound how many
requests are in flight at once, and asyncio.gather to launch all
num_requests coroutines together. The semaphore controls *how many can
run concurrently*, not whether they run at all -- every request is still
attempted, and every request still races every other in-flight request
against the seller exactly as it would in the real stampede this is
modeling. We are not queuing requests to avoid overwhelming the seller;
we are queuing them only so we can hit a precise, repeatable concurrency
level rather than firing all num_requests as one uncontrolled burst.
 
We deliberately do NOT retry failed requests, and we do NOT serialize
around errors. A failed request (HTTP error status, or a network-level
exception such as a timeout or connection error) is recorded as a
RequestOutcome with its status_code or error set, and the coroutine
returns -- it does not retry, and it does not block other in-flight
requests. That is intentional: the whole point of this instrument is to
surface how the naive seller behaves under real concurrent pressure, not
to paper over that behavior with client-side resilience.
"""
from __future__ import annotations
 
import asyncio
import random
import time
 
import httpx
 
from load_test.models import LoadTestConfig, PlannedRequest, RequestOutcome, new_request_id
 
 
def build_request_plan(config: LoadTestConfig, rng: random.Random) -> list[PlannedRequest]:
    """
    Build the full list of requests to fire, up front, before any HTTP
    calls happen. Deciding duplicates at plan time (rather than deciding
    randomly at fire time) means the set of "which requests are
    duplicates of which" is known and fixed before the run starts, so
    verification afterwards is checking against a plan, not a guess.
 
    Each planned request is either:
      - an "original": gets a freshly generated, never-before-used request_id
      - a "deliberate duplicate": reuses the request_id of an earlier
        original in the plan, chosen uniformly at random from the
        originals planned so far
 
    The first request in the plan is always an original -- there is
    nothing for it to duplicate yet.
    """
    plan: list[PlannedRequest] = []
    original_indices: list[int] = []
 
    for i in range(config.num_requests):
        user_id = f"user-{i}"
        make_duplicate = (
            i > 0
            and original_indices
            and rng.random() < config.duplicate_rate
        )
        if make_duplicate:
            source_index = rng.choice(original_indices)
            source = plan[source_index]
            plan.append(
                PlannedRequest(
                    index=i,
                    user_id=user_id,
                    request_id=source.request_id,
                    is_deliberate_duplicate=True,
                    duplicate_of_index=source_index,
                )
            )
        else:
            plan.append(
                PlannedRequest(
                    index=i,
                    user_id=user_id,
                    request_id=new_request_id(rng),
                    is_deliberate_duplicate=False,
                    duplicate_of_index=None,
                )
            )
            original_indices.append(i)
 
    return plan
 
 
async def _fire_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    planned: PlannedRequest,
    timeout_seconds: float,
) -> RequestOutcome:
    async with semaphore:
        payload = {"user_id": planned.user_id, "request_id": planned.request_id}
        start = time.perf_counter()
        try:
            response = await client.post("/buy", json=payload, timeout=timeout_seconds)
        except httpx.HTTPError as exc:
            # Network-level failure (timeout, connection error, etc). Recorded
            # as a failed outcome, NOT retried.
            latency = time.perf_counter() - start
            return RequestOutcome(
                planned=planned,
                status_code=None,
                ticket_number=None,
                latency_seconds=latency,
                error=f"{type(exc).__name__}: {exc}",
            )
        latency = time.perf_counter() - start
 
        ticket_number = None
        if response.status_code == 200:
            try:
                ticket_number = response.json().get("ticket_number")
            except ValueError:
                pass  # malformed body on a 200 -- leave ticket_number as None
 
        return RequestOutcome(
            planned=planned,
            status_code=response.status_code,
            ticket_number=ticket_number,
            latency_seconds=latency,
            error=None,
        )
 
 
async def run_load_test(
    config: LoadTestConfig,
    plan: list[PlannedRequest],
) -> list[RequestOutcome]:
    """
    Fire every planned request against the seller with up to
    `config.concurrency` in flight at once. All num_requests coroutines
    are launched together (not batched/serialized) so the actual
    concurrency profile hitting the seller matches what the semaphore
    allows, rather than an artificially staggered ramp.
    """
    semaphore = asyncio.Semaphore(config.concurrency)
    limits = httpx.Limits(
        max_connections=config.concurrency,
        max_keepalive_connections=config.concurrency,
    )
    async with httpx.AsyncClient(base_url=config.seller_url, limits=limits) as client:
        tasks = [
            _fire_one(client, semaphore, planned, config.timeout_seconds) for planned in plan
        ]
        outcomes = await asyncio.gather(*tasks)
    return list(outcomes)
 
 
async def reset_seller(config: LoadTestConfig) -> str:
    """Call POST /reset on the seller and return the new sale_id."""
    async with httpx.AsyncClient(base_url=config.seller_url) as client:
        response = await client.post(
            "/reset", json={"ticket_count": config.ticket_total}, timeout=config.timeout_seconds
        )
        response.raise_for_status()
        return response.json()["sale_id"]
 
 
async def fetch_status(config: LoadTestConfig) -> dict:
    """Call GET /status on the seller and return the parsed JSON body."""
    async with httpx.AsyncClient(base_url=config.seller_url) as client:
        response = await client.get("/status", timeout=config.timeout_seconds)
        response.raise_for_status()
        return response.json()
 