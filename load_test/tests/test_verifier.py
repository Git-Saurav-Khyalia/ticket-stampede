"""
Tests for verify(). These construct a synthetic plan, a synthetic
/status response, and synthetic RequestOutcomes by hand, so each
invariant's pass and fail path can be exercised deterministically
without a real seller.
"""
from load_test.models import PlannedRequest, RequestOutcome
from load_test.verifier import verify
 
 
def _planned(index, request_id, user_id=None, duplicate_of_index=None):
    return PlannedRequest(
        index=index,
        user_id=user_id or f"user-{index}",
        request_id=request_id,
        is_deliberate_duplicate=duplicate_of_index is not None,
        duplicate_of_index=duplicate_of_index,
    )
 
 
def _status(ticket_total, sold_count, sold_tickets):
    return {
        "sale_id": "sale-1",
        "ticket_total": ticket_total,
        "sold_count": sold_count,
        "sold_tickets": sold_tickets,
    }
 
 
def _sold(ticket_number, user_id):
    return {"ticket_number": ticket_number, "user_id": user_id, "sold_at": "2026-01-01T00:00:00Z"}
 
 
def _outcome(planned: PlannedRequest, status_code, ticket_number, latency=0.01, error=None):
    return RequestOutcome(
        planned=planned,
        status_code=status_code,
        ticket_number=ticket_number,
        latency_seconds=latency,
        error=error,
    )
 
 
def test_all_invariants_pass_on_a_clean_result():
    p0, p1, p2 = _planned(0, "req-a"), _planned(1, "req-b"), _planned(2, "req-c")
    plan = [p0, p1, p2]
    status = _status(
        ticket_total=3,
        sold_count=3,
        sold_tickets=[
            _sold(1, "user-0"),
            _sold(2, "user-1"),
            _sold(3, "user-2"),
        ],
    )
    outcomes = [
        _outcome(p0, 200, 1),
        _outcome(p1, 200, 2),
        _outcome(p2, 200, 3),
    ]
    result = verify(plan, status, outcomes)
    assert result.overall_passed
    assert all(c.passed for c in result.checks)
 
 
def test_overselling_is_detected():
    plan = [_planned(i, f"req-{i}") for i in range(3)]
    # ticket_total is 2, but 3 rows are marked SOLD
    status = _status(
        ticket_total=2,
        sold_count=3,
        sold_tickets=[_sold(1, "user-0"), _sold(2, "user-1"), _sold(3, "user-2")],
    )
    outcomes = [_outcome(p, 200, n) for p, n in zip(plan, [1, 2, 3])]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "No overselling")
    assert not check.passed
    assert not result.overall_passed
 
 
def test_duplicate_ticket_number_is_detected_in_final_status():
    plan = [_planned(0, "req-a"), _planned(1, "req-b")]
    # Same ticket_number (5) present twice in the FINAL /status rows.
    status = _status(
        ticket_total=10,
        sold_count=2,
        sold_tickets=[_sold(5, "user-0"), _sold(5, "user-1")],
    )
    outcomes = [_outcome(plan[0], 200, 5), _outcome(plan[1], 200, 5)]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Unique tickets")
    assert not check.passed
    assert "5" in check.detail
 
 
def test_idempotency_violation_when_duplicate_request_id_both_win():
    # Two planned requests share request_id "dup-1" (a deliberate
    # duplicate) and BOTH show up as sold rows in /status -- exactly the
    # naive-seller failure mode this invariant exists to catch.
    original = _planned(0, "dup-1", user_id="user-0")
    duplicate = _planned(1, "dup-1", user_id="user-1", duplicate_of_index=0)
    plan = [original, duplicate]
 
    status = _status(
        ticket_total=10,
        sold_count=2,
        sold_tickets=[_sold(1, "user-0"), _sold(2, "user-1")],
    )
    outcomes = [_outcome(original, 200, 1), _outcome(duplicate, 200, 2)]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Request idempotency")
    assert not check.passed
    assert "dup-1" in check.detail
    assert not result.overall_passed
 
 
def test_idempotency_holds_when_duplicate_request_id_only_wins_once():
    original = _planned(0, "dup-1", user_id="user-0")
    duplicate = _planned(1, "dup-1", user_id="user-1", duplicate_of_index=0)
    plan = [original, duplicate]
 
    # Only user-0's request actually resulted in a sold ticket; user-1's
    # duplicate correctly did not (e.g. it received a sold-out response).
    status = _status(
        ticket_total=10,
        sold_count=1,
        sold_tickets=[_sold(1, "user-0")],
    )
    outcomes = [_outcome(original, 200, 1), _outcome(duplicate, 409, None)]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Request idempotency")
    assert check.passed
 
 
def test_idempotency_check_ignores_request_ids_that_were_never_duplicated():
    plan = [_planned(0, "req-a"), _planned(1, "req-b")]
    status = _status(
        ticket_total=10, sold_count=2, sold_tickets=[_sold(1, "user-0"), _sold(2, "user-1")]
    )
    outcomes = [_outcome(plan[0], 200, 1), _outcome(plan[1], 200, 2)]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Request idempotency")
    assert check.passed
 
 
def test_status_consistency_violation_when_sold_count_does_not_match_rows():
    plan = [_planned(0, "req-a")]
    # sold_count claims 5, but only 1 row is actually present.
    status = _status(ticket_total=10, sold_count=5, sold_tickets=[_sold(1, "user-0")])
    outcomes = [_outcome(plan[0], 200, 1)]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Status consistency")
    assert not check.passed
    assert not result.overall_passed
 
 
def test_overall_passed_is_false_if_any_single_check_fails():
    plan = [_planned(0, "req-a"), _planned(1, "req-b")]
    # Unique tickets check will fail; everything else would pass.
    status = _status(
        ticket_total=10,
        sold_count=2,
        sold_tickets=[_sold(1, "user-0"), _sold(1, "user-1")],
    )
    outcomes = [_outcome(plan[0], 200, 1), _outcome(plan[1], 200, 1)]
    result = verify(plan, status, outcomes)
    assert not result.overall_passed
 
 
# --- New: "Successful response ticket uniqueness" ---------------------------
# These test the raw-outcomes-level check, independent of what /status shows.
 
 
def test_response_uniqueness_passes_when_successful_responses_return_different_tickets():
    p0, p1 = _planned(0, "req-a"), _planned(1, "req-b")
    plan = [p0, p1]
    status = _status(
        ticket_total=10, sold_count=2, sold_tickets=[_sold(1, "user-0"), _sold(2, "user-1")]
    )
    outcomes = [_outcome(p0, 200, 1), _outcome(p1, 200, 2)]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Successful response ticket uniqueness")
    assert check.passed
 
 
def test_response_uniqueness_fails_when_same_ticket_returned_to_two_different_request_ids():
    # Two DIFFERENT request_ids both received an HTTP 200 claiming
    # ticket_number 7 -- this is the race-condition symptom the take-home
    # describes (Experiment 1), and can occur even if /status later shows
    # only one row for ticket 7 (the other write having been silently
    # overwritten).
    p0, p1 = _planned(0, "req-a", user_id="user-0"), _planned(1, "req-b", user_id="user-1")
    plan = [p0, p1]
    # /status shows only ONE row for ticket 7 -- final state looks clean.
    status = _status(ticket_total=10, sold_count=1, sold_tickets=[_sold(7, "user-1")])
    outcomes = [_outcome(p0, 200, 7), _outcome(p1, 200, 7)]
 
    result = verify(plan, status, outcomes)
 
    # The final-state check (based on /status) sees nothing wrong...
    final_state_check = next(c for c in result.checks if c.name == "Unique tickets")
    assert final_state_check.passed
 
    # ...but the response-level check catches it, because two DIFFERENT
    # request_ids were both told they got ticket 7.
    response_check = next(
        c for c in result.checks if c.name == "Successful response ticket uniqueness"
    )
    assert not response_check.passed
    assert "7" in response_check.detail
    assert "req-a" in response_check.detail
    assert "req-b" in response_check.detail
 
    assert not result.overall_passed
 
 
def test_response_uniqueness_passes_when_same_ticket_returned_repeatedly_for_one_request_id():
    # Idempotent retries: the SAME request_id (e.g. a client retrying
    # after a timeout, or several different users mistakenly reusing one
    # request_id) is told about the same ticket multiple times. This is
    # correct behavior, not a violation -- it must PASS, and must not
    # contradict "Request idempotency" passing on the same data. This is
    # the exact shape of the false positive reported against Experiment 2
    # (20 tickets, 100 requests, concurrency 1, duplicate rate 50%):
    # ticket #1 legitimately returned 5 times, all for one request_id.
    same_request_id = "bdd640fb06671ad11c80317fa3b1799d"
    p0 = _planned(0, same_request_id, user_id="user-0")
    p1 = _planned(1, same_request_id, user_id="user-1", duplicate_of_index=0)
    p2 = _planned(2, same_request_id, user_id="user-2", duplicate_of_index=0)
    p3 = _planned(3, same_request_id, user_id="user-3", duplicate_of_index=0)
    p4 = _planned(4, same_request_id, user_id="user-4", duplicate_of_index=0)
    plan = [p0, p1, p2, p3, p4]
 
    # Only one ticket was ever actually consumed -- /status shows one row.
    status = _status(ticket_total=20, sold_count=1, sold_tickets=[_sold(1, "user-0")])
    outcomes = [
        _outcome(p0, 200, 1),
        _outcome(p1, 200, 1),
        _outcome(p2, 200, 1),
        _outcome(p3, 200, 1),
        _outcome(p4, 200, 1),
    ]
 
    result = verify(plan, status, outcomes)
 
    response_check = next(
        c for c in result.checks if c.name == "Successful response ticket uniqueness"
    )
    assert response_check.passed
 
    # And this must agree with "Request idempotency" -- both describe
    # the same underlying correct behavior, from two different angles.
    idempotency_check = next(c for c in result.checks if c.name == "Request idempotency")
    assert idempotency_check.passed
 
    assert result.overall_passed
 
 
def test_response_uniqueness_ignores_failed_requests_with_no_ticket_number():
    p0, p1 = _planned(0, "req-a"), _planned(1, "req-b")
    plan = [p0, p1]
    status = _status(ticket_total=1, sold_count=1, sold_tickets=[_sold(1, "user-0")])
    outcomes = [
        _outcome(p0, 200, 1),
        _outcome(p1, 409, None),  # sold-out response -- no ticket_number, must be ignored
    ]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Successful response ticket uniqueness")
    assert check.passed
 
 
def test_response_uniqueness_ignores_network_level_failures_with_no_status_code():
    p0, p1 = _planned(0, "req-a"), _planned(1, "req-b")
    plan = [p0, p1]
    status = _status(ticket_total=1, sold_count=1, sold_tickets=[_sold(1, "user-0")])
    outcomes = [
        _outcome(p0, 200, 1),
        _outcome(p1, None, None, error="ConnectTimeout"),  # never got a response at all
    ]
    result = verify(plan, status, outcomes)
    check = next(c for c in result.checks if c.name == "Successful response ticket uniqueness")
    assert check.passed
 