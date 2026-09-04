Experiments: Version 1 failures and Version 2 verification

This document exists to satisfy the take-home's requirement to "include both the failing run and the passing run in your write-up." Three distinct phases are documented below, in order, and kept clearly separate:

Version 1 failing evidence — the naive seller, actually measured.
An intermediate verifier bug, discovered while validating Version 2 — a bug in the load tester's checking logic, not the seller.
Corrected Version 2 passing evidence — the actual final measured result, seller and verifier both correct.
Experiment 1: concurrent unique buyers (the row-race)
1. Version 1 failing evidence (actual, measured)
Command shape: 5 tickets, 50 requests, concurrency 50, duplicate rate 0%, seed 42

Result:
  No overselling:                        PASS
  Unique tickets:                        PASS
  Request idempotency:                   PASS
  Status consistency:                    PASS
  Successful response ticket uniqueness: FAIL

Verifier observed:
  - ticket #2 returned in 14 successful HTTP 200 responses to different requests
  - ticket #4 returned in 2 successful HTTP 200 responses
  - ticket #5 returned in 2 successful HTTP 200 responses
3. Corrected Version 2 passing evidence (actual, measured)
Command:
python -m load_test.cli --tickets 5 --requests 50 --concurrency 50 --duplicate-rate 0 --seed 42

Tickets: 5
Requests: 50
Concurrency: 50
Duplicate rate: 0%

Requests completed: 50/50
Successful purchases: 5
Failed requests: 45
Throughput: 28.9 req/s
Median latency: 927.8 ms
P99 latency: 997.4 ms
Total duration: 1.73 s

No overselling:                        PASS
Unique tickets:                        PASS
Request idempotency:                   PASS
Status consistency:                    PASS
Successful response ticket uniqueness: PASS
Overall:                                PASS

50 concurrent requests for 5 tickets: exactly 5 succeed, 45 correctly receive a sold-out failure, and — the specific thing Version 1 failed — Successful response ticket uniqueness now passes: no ticket number was returned via a successful response to more than one distinct request_id.

Why the Version 2 design fixes this failure

The Version 1 failure came from find_next_available() reading the lowest-numbered AVAILABLE ticket with a plain, unlocked SELECT: many concurrent requests could read the same row as AVAILABLE before any of their writes landed, and mark_sold()'s unconditional UPDATE let every one of them succeed, each silently overwriting the last. Version 2 replaces the read with SELECT ... FOR UPDATE: a concurrent reader targeting the same row must block until the transaction holding it commits or rolls back, and once it does, the blocked reader's WHERE status = AVAILABLE is re-evaluated against the now-committed row — which is SOLD — so it moves on to a different row instead of also returning the one just claimed. Concurrent buyers converge on a given row one at a time, never simultaneously, which is what this experiment's shape is designed to test, and what the passing run above confirms.

Experiment 2: sequential + concurrent duplicate request_id
1. Version 1 failing evidence (actual, measured)
Command shape: 20 tickets, 100 requests, concurrency 1, duplicate rate 50%

Result:
  Request idempotency: FAILED
  Same request_id produced multiple successful purchases.

This reproduced at concurrency 1 — no race was required. Nothing in Version 1 checked whether a request_id had been used before, so a repeated request_id was simply sold a second, brand-new ticket.

2. Intermediate verifier bug, discovered during Version 2 validation

Once Version 2's seller logic was in place, the same command shape was run against it and produced this intermediate result — the seller was already correct at this point, but the load tester's own checking logic was not:

Command:
python -m load_test.cli --tickets 20 --requests 100 --concurrency 1 --duplicate-rate 0.5 --seed 42

Result:
  No overselling:                        PASS
  Unique tickets:                        PASS
  Request idempotency:                   PASS
  Status consistency:                    PASS
  Successful response ticket uniqueness: FAIL
  Overall:                                FAIL

Verifier observed: ticket #1 returned in 5 successful HTTP 200 responses,
all 5 sharing the exact same request_id: bdd640fb06671ad11c80317fa3b1799d

Request idempotency (which reads GET /status and checks that no duplicated request_id produced more than one sold row) already passed — the seller behaved correctly. The FAIL on Successful response ticket uniqueness was a bug in the verifier, not the seller: that check originally flagged any ticket number appearing in more than one successful HTTP response, without checking whether those responses shared a request_id. A client legitimately retrying one request_id five times and correctly getting the same ticket back five times is not a violation — it's the desired idempotent behavior — but the pre-fix verifier couldn't distinguish that from two genuinely different request_ids both being told they hold the same ticket (Experiment 1's actual failure mode). The verifier was corrected to group successful responses by ticket number and then check the distinct request_id count for that ticket, rather than the raw response count.

This intermediate FAIL is not the final Version 2 result — it is recorded here only as the evidence that motivated the verifier fix.

3. Corrected Version 2 passing evidence (actual, measured)
Command:
python -m load_test.cli --tickets 20 --requests 100 --concurrency 1 --duplicate-rate 0.5 --seed 42

Tickets: 20
Requests: 100
Concurrency: 1
Duplicate rate: 50%

Requests completed: 100/100
Successful purchases: 52
Failed requests: 48
Throughput: 37.3 req/s
Median latency: 18.0 ms
P99 latency: 32.7 ms
Total duration: 2.68 s

No overselling:                        PASS
Unique tickets:                        PASS
Request idempotency:                   PASS
Status consistency:                    PASS
Successful response ticket uniqueness: PASS
Overall:                                PASS
Why the Version 2 design fixes the underlying seller failure

Version 1 had no mechanism at all for detecting a repeated request_id. Version 2 adds a UNIQUE(sale_id, request_id) constraint, with the database constraint serving as the final authority for concurrent idempotency: a fast, unlocked lookup short-circuits the common case (a request_id that already has a ticket returns that ticket immediately, no allocation attempt), and if two concurrent requests both miss that lookup and both attempt to claim a ticket, whichever commits first wins the request_id; the loser's commit fails the constraint with IntegrityError, which is caught, rolled back, and resolved by looking up and returning the ticket the winner already committed. Neither path can ever result in the same request_id being sold two different tickets.

Why 52 successful HTTP responses with 20 tickets is valid

With --duplicate-rate 0.5 over 100 planned requests, roughly half are "original" requests (each carrying a request_id never used before in the plan) and roughly half are deliberate duplicates that reuse an earlier original's request_id. At --concurrency 1 (fully sequential) with ticket_total = 20, the seller sells its 20 tickets to the first 20 distinct request_ids it sees; every request after that either:

carries a request_id that already has a ticket (an original that already won, or a duplicate of one) — the fast-path lookup finds it and returns the same ticket with a 200, or
carries a brand-new request_id once all 20 tickets are already gone — this gets SOLD_OUT (a Failed request in the count above, not a successful response).

So the 52 successful (200) responses are not 52 tickets — they are 52 HTTP responses that collectively resolve to at most 20 distinct ticket numbers, because every response beyond the 20 that first claimed a ticket is an idempotent replay of a request_id that already had one. This is exactly what No overselling: PASS (sold rows ≤ 20) and Request idempotency: PASS (no request_id resolved to more than one sold ticket) together certify: more successful responses than tickets is expected and correct whenever some of those responses are repeats of an already-settled request_id, which is precisely the duplicate-rate-0.5 scenario this experiment is built to exercise. The corrected Successful response ticket uniqueness check adds the complementary guarantee from the raw-response side: it checks that the same ticket is not associated with different request_ids — each of those ≤20 ticket numbers was told to exactly one request_id, however many times that request_id's responses repeat it.

Test suite
python -m pytest
38 passed in 1.76s