"""
Independent correctness verification.

The critical design point here: final-state verification is driven by
GET /status -- the seller's own durable ground truth -- not by counting
the HTTP responses this client happened to receive. A client-side "it got
a 200" is not proof a ticket was actually, durably issued (the response
could be lost, the seller could crash after responding, etc.), and
trusting it as the ONLY signal would let a seller that lies in its
response body pass a check it shouldn't. So "No overselling", "Unique
tickets", "Request idempotency", and "Status consistency" below are all
checked against the rows /status reports, joined back to the request plan
by `user_id`.

The join works because each PlannedRequest is given a unique `user_id`
(one per planned index, even for deliberate duplicates) at plan-building
time in client.py. /status's sold_tickets includes user_id, so a sold
ticket's user_id tells us unambiguously which PlannedRequest it came
from -- and multiple PlannedRequests can share a request_id (that's the
duplicate scenario), which is exactly what "Request idempotency" needs
to detect.

/status alone, however, cannot see everything worth knowing. It reports
only the seller's final row-per-ticket state; if the naive seller's race
condition let two concurrent buyers each receive an HTTP 200 with the
same ticket_number, and one of those writes was later silently
overwritten by the other (see the race described in app/service.py),
/status will only ever show one row for that ticket number. Checking
/status alone would then report "Unique tickets: PASS" despite two
buyers genuinely having been told, over HTTP, that they got the same
ticket. That is a real, distinct failure the take-home's own
interleaving example describes, and it deserves its own invariant rather
than being folded into (or mistaken for) the final-state check.

"Successful response ticket uniqueness" below exists for exactly that:
it inspects the raw RequestOutcomes this client actually received --
HTTP 200s only -- and checks whether any ticket_number was handed out in
more than one successful response. It complements /status verification;
it does not replace it. The two checks answer two different questions:
    - /status: "does the final database contain duplicate ticket rows?"
    - outcomes: "did two successful HTTP responses tell different buyers
      they got the same ticket?"
A seller can fail either one independently of the other.
"""
from __future__ import annotations

from dataclasses import dataclass

from load_test.models import PlannedRequest, RequestOutcome


@dataclass(frozen=True)
class InvariantCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class VerificationResult:
    checks: list[InvariantCheck]

    @property
    def overall_passed(self) -> bool:
        return all(c.passed for c in self.checks)


def verify(
    plan: list[PlannedRequest],
    status: dict,
    outcomes: list[RequestOutcome],
) -> VerificationResult:
    ticket_total = status["ticket_total"]
    sold_count = status["sold_count"]
    sold_tickets = status["sold_tickets"]  # list of {ticket_number, user_id, sold_at}

    checks = [
        _check_no_overselling(ticket_total, sold_tickets),
        _check_unique_ticket_numbers(sold_tickets),
        _check_request_idempotency(plan, sold_tickets),
        _check_status_consistency(sold_count, sold_tickets),
        _check_successful_response_ticket_uniqueness(outcomes),
    ]
    return VerificationResult(checks=checks)


def _check_no_overselling(ticket_total: int, sold_tickets: list[dict]) -> InvariantCheck:
    actual_sold = len(sold_tickets)
    passed = actual_sold <= ticket_total
    detail = (
        f"ticket_total={ticket_total}, actual sold rows={actual_sold}"
        if passed
        else f"OVERSOLD: ticket_total={ticket_total} but {actual_sold} rows are marked SOLD"
    )
    return InvariantCheck(name="No overselling", passed=passed, detail=detail)


def _check_unique_ticket_numbers(sold_tickets: list[dict]) -> InvariantCheck:
    numbers = [t["ticket_number"] for t in sold_tickets]
    seen: set[int] = set()
    duplicates: set[int] = set()
    for n in numbers:
        if n in seen:
            duplicates.add(n)
        seen.add(n)

    passed = len(duplicates) == 0
    detail = (
        "all sold ticket numbers are unique"
        if passed
        else f"DUPLICATE ticket numbers issued to multiple buyers: {sorted(duplicates)}"
    )
    return InvariantCheck(name="Unique tickets", passed=passed, detail=detail)


def _check_request_idempotency(
    plan: list[PlannedRequest], sold_tickets: list[dict]
) -> InvariantCheck:
    # user_id -> ticket_number, for every planned request that actually
    # resulted in a sold row according to the seller's own state.
    sold_user_ids = {t["user_id"] for t in sold_tickets if t.get("user_id") is not None}

    # Group planned requests by request_id (this is where deliberate
    # duplicates -- multiple planned requests sharing one request_id --
    # show up).
    by_request_id: dict[str, list[PlannedRequest]] = {}
    for planned in plan:
        by_request_id.setdefault(planned.request_id, []).append(planned)

    violations: list[str] = []
    for request_id, planned_requests in by_request_id.items():
        if len(planned_requests) < 2:
            continue  # not a duplicated request_id, nothing to check
        winners = [p for p in planned_requests if p.user_id in sold_user_ids]
        if len(winners) > 1:
            winner_users = ", ".join(p.user_id for p in winners)
            violations.append(
                f"request_id={request_id} produced {len(winners)} successful purchases "
                f"(users: {winner_users})"
            )

    passed = len(violations) == 0
    detail = (
        "every duplicated request_id resulted in at most one sold ticket"
        if passed
        else "IDEMPOTENCY VIOLATION(S): " + "; ".join(violations)
    )
    return InvariantCheck(name="Request idempotency", passed=passed, detail=detail)


def _check_status_consistency(sold_count: int, sold_tickets: list[dict]) -> InvariantCheck:
    actual = len(sold_tickets)
    passed = sold_count == actual
    detail = (
        f"sold_count={sold_count} matches {actual} actual sold rows"
        if passed
        else f"MISMATCH: /status reports sold_count={sold_count} but returned {actual} sold ticket rows"
    )
    return InvariantCheck(name="Status consistency", passed=passed, detail=detail)


def _check_successful_response_ticket_uniqueness(
    outcomes: list[RequestOutcome],
) -> InvariantCheck:
    """
    Checks the raw HTTP responses this client received -- NOT /status.

    Looks only at outcomes with status_code == 200 (a request that failed,
    or never got a response at all, has no ticket_number and is ignored
    here). For each such successful outcome, records which PlannedRequest
    (identified by its unique user_id/request_id) was told it received
    which ticket_number. If the same ticket_number was successfully
    returned to more than one distinct PlannedRequest, that is a failure:
    two buyers were each told, via a 200 response, that they hold the
    same ticket -- independent of whatever /status shows afterward.
    """
    successful = [o for o in outcomes if o.status_code == 200 and o.ticket_number is not None]

    by_ticket_number: dict[int, list[RequestOutcome]] = {}
    for outcome in successful:
        by_ticket_number.setdefault(outcome.ticket_number, []).append(outcome)

    violations: list[str] = []
    for ticket_number, winners in by_ticket_number.items():
        if len(winners) < 2:
            continue
        affected = ", ".join(
            f"(user={o.planned.user_id}, request_id={o.planned.request_id})" for o in winners
        )
        violations.append(
            f"ticket_number={ticket_number} was returned in {len(winners)} successful "
            f"HTTP 200 responses to different requests: {affected}"
        )

    passed = len(violations) == 0
    detail = (
        "every successful HTTP response returned a distinct ticket_number"
        if passed
        else "DUPLICATE SUCCESSFUL RESPONSE(S): " + "; ".join(violations)
    )
    return InvariantCheck(
        name="Successful response ticket uniqueness", passed=passed, detail=detail
    )
