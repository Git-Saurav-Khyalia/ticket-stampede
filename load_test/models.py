"""
Data models for the load tester.
 
These are plain dataclasses, not pydantic -- there is no HTTP boundary
inside this package that needs validation; everything here is either
generated in-process (PlannedRequest) or the record of an HTTP call this
process itself made (RequestOutcome).
"""
from __future__ import annotations
 
import random
from dataclasses import dataclass, field
 
 
@dataclass(frozen=True)
class LoadTestConfig:
    seller_url: str
    ticket_total: int
    num_requests: int
    concurrency: int
    duplicate_rate: float  # 0.0 - 1.0, fraction of requests reusing an earlier request_id
    timeout_seconds: float = 10.0
    reset_before_run: bool = True
 
    def __post_init__(self):
        if not (0.0 <= self.duplicate_rate <= 1.0):
            raise ValueError("duplicate_rate must be between 0.0 and 1.0")
        if self.num_requests <= 0:
            raise ValueError("num_requests must be positive")
        if self.concurrency <= 0:
            raise ValueError("concurrency must be positive")
 
 
@dataclass(frozen=True)
class PlannedRequest:
    """
    One request the client intends to fire. Built up-front, before any
    HTTP calls are made, so that duplicate request_ids are a deliberate
    property of the plan rather than an accident of timing.
    """
    index: int
    user_id: str
    request_id: str
    is_deliberate_duplicate: bool  # True if this reuses an earlier PlannedRequest's request_id
    duplicate_of_index: int | None  # index of the original request this duplicates, if any
 
 
@dataclass
class RequestOutcome:
    """The observed result of actually firing one PlannedRequest."""
    planned: PlannedRequest
    status_code: int | None  # None if the request never got an HTTP response (network error)
    ticket_number: int | None
    latency_seconds: float
    error: str | None  # exception class/message, only set when status_code is None
 
 
def new_request_id(rng: random.Random) -> str:
    """
    Generate a request_id deterministically from the supplied RNG, so
    that build_request_plan(config, rng) is fully reproducible for a
    given seed. 128 bits of RNG-derived entropy per id keeps collisions
    within one plan astronomically unlikely, the same guarantee uuid4
    provided -- the only change is the entropy now comes from `rng`
    instead of the OS's nondeterministic source.
    """
    return f"{rng.getrandbits(128):032x}"
