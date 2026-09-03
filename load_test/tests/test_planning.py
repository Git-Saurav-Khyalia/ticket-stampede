"""
Tests for build_request_plan. These are pure unit tests -- no network,
no seller required -- since plan-building is deterministic computation
given a seeded RNG.
"""
import random

from load_test.client import build_request_plan
from load_test.models import LoadTestConfig


def _config(**overrides) -> LoadTestConfig:
    defaults = dict(
        seller_url="http://localhost:8000",
        ticket_total=100,
        num_requests=200,
        concurrency=50,
        duplicate_rate=0.2,
    )
    defaults.update(overrides)
    return LoadTestConfig(**defaults)


def test_plan_has_exactly_num_requests_entries():
    config = _config(num_requests=250)
    plan = build_request_plan(config, random.Random(1))
    assert len(plan) == 250


def test_first_request_is_never_a_duplicate():
    config = _config(duplicate_rate=1.0)  # maximize the chance of catching a bug here
    plan = build_request_plan(config, random.Random(1))
    assert plan[0].is_deliberate_duplicate is False
    assert plan[0].duplicate_of_index is None


def test_zero_duplicate_rate_produces_no_duplicates():
    config = _config(duplicate_rate=0.0, num_requests=500)
    plan = build_request_plan(config, random.Random(1))
    assert all(not p.is_deliberate_duplicate for p in plan)
    # every request_id must be unique
    assert len({p.request_id for p in plan}) == len(plan)


def test_duplicate_requests_reuse_an_earlier_original_request_id():
    config = _config(duplicate_rate=0.5, num_requests=500)
    plan = build_request_plan(config, random.Random(42))

    duplicates = [p for p in plan if p.is_deliberate_duplicate]
    assert len(duplicates) > 0  # sanity check the test itself is exercising duplication

    for dup in duplicates:
        assert dup.duplicate_of_index is not None
        assert dup.duplicate_of_index < dup.index  # can only duplicate something earlier
        source = plan[dup.duplicate_of_index]
        assert source.request_id == dup.request_id
        assert source.is_deliberate_duplicate is False  # duplicates always point at an original


def test_every_planned_request_has_a_unique_user_id():
    # user_id uniqueness is what lets the verifier join /status rows back
    # to specific planned requests, even across duplicated request_ids.
    config = _config(duplicate_rate=0.3, num_requests=300)
    plan = build_request_plan(config, random.Random(7))
    assert len({p.user_id for p in plan}) == len(plan)


def test_duplicate_rate_is_approximately_respected_over_a_large_plan():
    config = _config(duplicate_rate=0.25, num_requests=2000)
    plan = build_request_plan(config, random.Random(3))
    duplicate_fraction = sum(p.is_deliberate_duplicate for p in plan) / len(plan)
    # Not exact (it's randomized, and the first request can never be a
    # duplicate), but should land in a reasonable band around 0.25.
    assert 0.15 < duplicate_fraction < 0.35


def test_plan_is_reproducible_given_the_same_seed():
    config = _config(duplicate_rate=0.2, num_requests=100)
    plan_a = build_request_plan(config, random.Random(99))
    plan_b = build_request_plan(config, random.Random(99))
    assert [p.request_id for p in plan_a] == [p.request_id for p in plan_b]
    assert [p.is_deliberate_duplicate for p in plan_a] == [
        p.is_deliberate_duplicate for p in plan_b
    ]
