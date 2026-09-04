"""
Concurrency / integration tests for Version 2.
 
These are NOT unit tests with a mocked database -- they run the actual
FastAPI app against the real Postgres instance from docker-compose.yml
(the same one tests/conftest.py's fixtures use), firing genuinely
concurrent requests from multiple threads. An in-memory or mocked-DB
test cannot prove anything about PostgreSQL's row-level locking; only a
test that hits real Postgres under real concurrent access can.
 
Each concurrent request is fired from its own worker thread. The
`get_db` dependency override (see conftest.py) creates a fresh SQLAlchemy
session per call, and the underlying engine's connection pool hands out
a separate real connection per thread, so this genuinely exercises
concurrent transactions against Postgres rather than serializing
everything through one shared connection.
 
These tests are the direct, positive counterpart to the two experiments
that proved Version 1 broken:
  - Experiment 1 (5 tickets, 50 requests, concurrency 50, dup rate 0%)
    proved the same ticket could be returned to multiple buyers.
  - Experiment 2 (20 tickets, 100 requests, concurrency 1, dup rate 50%)
    proved a repeated request_id could be sold twice.
test_concurrent_unique_requests_never_return_the_same_ticket and
test_concurrent_duplicate_request_id_requests_all_resolve_to_the_same_ticket
below are structured to reproduce conditions similar to each experiment
and assert the failure no longer occurs.
 
test_concurrent_duplicate_request_id_with_one_ticket is a third,
narrower regression test for a bug found after those two: when the
LAST available ticket is claimed by one of two concurrent requests
sharing the same request_id, the loser must not be told SOLD_OUT --
its request_id was, in fact, already fulfilled by the winner. See
app/service.py buy_ticket()'s "no AVAILABLE row" branch for the fix.
"""
import concurrent.futures
 
 
def test_concurrent_unique_requests_never_return_the_same_ticket(client):
    ticket_total = 20
    client.post("/reset", json={"ticket_count": ticket_total})
 
    def buy(i: int):
        return client.post(
            "/buy", json={"user_id": f"user-{i}", "request_id": f"req-{i}"}
        )
 
    with concurrent.futures.ThreadPoolExecutor(max_workers=ticket_total) as executor:
        responses = list(executor.map(buy, range(ticket_total)))
 
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.json()) for r in responses if r.status_code != 200
    ]
 
    ticket_numbers = [r.json()["ticket_number"] for r in responses]
    # The core invariant this test exists to prove: no two concurrent,
    # distinct requests were ever told they got the same ticket.
    assert len(set(ticket_numbers)) == ticket_total, (
        f"expected {ticket_total} distinct ticket numbers, got "
        f"{len(set(ticket_numbers))}: {sorted(ticket_numbers)}"
    )
    assert sorted(ticket_numbers) == list(range(1, ticket_total + 1))
 
    status = client.get("/status").json()
    assert status["sold_count"] == ticket_total
    assert len(status["sold_tickets"]) == ticket_total
 
 
def test_concurrent_duplicate_request_id_requests_all_resolve_to_the_same_ticket(client):
    ticket_total = 20
    client.post("/reset", json={"ticket_count": ticket_total})
 
    duplicate_request_id = "same-request-id"
 
    def buy(i: int):
        return client.post(
            "/buy", json={"user_id": f"user-{i}", "request_id": duplicate_request_id}
        )
 
    concurrency = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        responses = list(executor.map(buy, range(concurrency)))
 
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.json()) for r in responses if r.status_code != 200
    ]
 
    ticket_numbers = {r.json()["ticket_number"] for r in responses}
    # The core invariant this test exists to prove: every one of these
    # concurrent requests, despite racing for the same request_id, was
    # told about the SAME single ticket -- not each grabbing a distinct
    # one.
    assert len(ticket_numbers) == 1, (
        f"expected all {concurrency} responses to agree on one ticket, "
        f"got {len(ticket_numbers)} distinct ticket numbers: {ticket_numbers}"
    )
 
    # And only ONE ticket was actually consumed from the pool -- the
    # other (concurrency - 1) available tickets remain untouched.
    status = client.get("/status").json()
    assert status["sold_count"] == 1
    assert len(status["sold_tickets"]) == 1
    assert status["sold_tickets"][0]["ticket_number"] == next(iter(ticket_numbers))
 
 
def test_concurrent_duplicate_request_id_with_one_ticket(client):
    """
    Regression test for the SOLD_OUT/idempotency race: with only ONE
    ticket available, two concurrent requests sharing the same
    request_id must NOT let the loser see SOLD_OUT. One of them commits
    the ticket; the other, finding no AVAILABLE row left, must re-check
    (sale_id, request_id) and discover its own request_id was already
    fulfilled by the winner -- not conclude the sale is out of tickets.
 
    Both requests must succeed (HTTP 200), both must report ticket #1,
    and exactly one ticket must actually be consumed.
    """
    client.post("/reset", json={"ticket_count": 1})
 
    duplicate_request_id = "only-one-ticket-same-id"
 
    def buy(i: int):
        return client.post(
            "/buy", json={"user_id": f"user-{i}", "request_id": duplicate_request_id}
        )
 
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(buy, range(2)))
 
    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.json()) for r in responses
    ]
    assert all(r.json()["ticket_number"] == 1 for r in responses), [
        r.json() for r in responses
    ]
 
    status = client.get("/status").json()
    assert status["sold_count"] == 1
    assert len(status["sold_tickets"]) == 1
    assert status["sold_tickets"][0]["ticket_number"] == 1
 
 
def test_concurrent_mixed_unique_and_duplicate_requests_final_status_is_consistent(client):
    """
    A closer approximation of a real stampede: mostly-unique requests
    with some deliberate request_id repeats mixed in, all fired at once.
    Verifies GET /status agrees with reality (sold_count matches actual
    rows, no duplicate ticket numbers, total sold never exceeds
    ticket_total) after the dust settles -- the same four invariants the
    load tester's verifier checks, exercised here directly against the
    app rather than through the load_test package.
    """
    ticket_total = 15
    client.post("/reset", json={"ticket_count": ticket_total})
 
    # 30 requests: 15 unique request_ids, each fired twice (a deliberate
    # duplicate), all at once.
    request_ids = [f"req-{i}" for i in range(15)]
    calls = [(f"user-{i}-a", rid) for i, rid in enumerate(request_ids)] + [
        (f"user-{i}-b", rid) for i, rid in enumerate(request_ids)
    ]
 
    def buy(call):
        user_id, request_id = call
        return client.post("/buy", json={"user_id": user_id, "request_id": request_id})
 
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as executor:
        responses = list(executor.map(buy, calls))
 
    assert all(r.status_code == 200 for r in responses)
 
    status = client.get("/status").json()
    sold_tickets = status["sold_tickets"]
 
    # No overselling.
    assert len(sold_tickets) <= ticket_total
    # No duplicate ticket numbers in the final state.
    numbers = [t["ticket_number"] for t in sold_tickets]
    assert len(numbers) == len(set(numbers))
    # /status internal consistency.
    assert status["sold_count"] == len(sold_tickets)
    # Each of the 15 unique request_ids should have claimed exactly one
    # ticket -- 30 calls (15 request_ids x 2 each), 15 tickets total.
    assert len(sold_tickets) == 15
 