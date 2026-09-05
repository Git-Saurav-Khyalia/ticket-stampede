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

Performance investigation: concurrency vs. latency, throughput, and correctness

This document records a capacity investigation of the Version 2 seller, run at increasing concurrency levels with load_test.cli against a fixed ticket pool, to find where the system's performance degrades and where it breaks outright. All correctness invariants are checked at every concurrency level; the focus here is latency/throughput behavior, not correctness (correctness is already covered in EXPERIMENTS.md).

Two kinds of statements appear below, and they are kept clearly separate throughout: measured evidence (numbers and log lines actually observed from a run) and architectural inference (explanations grounded in the code's known design, but not directly measured in this investigation).

Measured results
Concurrency 10
Requests: 1000
Successful purchases: 1000
Failed requests: 0
Throughput: 104.3 req/s
Median latency: 74.2 ms
P99 latency: 461.2 ms
Duration: 9.59 s
All invariants: PASS
Concurrency 25
Requests: 1000
Successful purchases: 1000
Failed requests: 0
Throughput: 100.3 req/s
Median latency: 147.8 ms
P99 latency: 1110.0 ms
Duration: 9.97 s
All invariants: PASS
Concurrency 50 (two independent runs)
Run 1:
  999/1000 completed, 999 successful purchases, 1 failure
  Throughput: 66.1 req/s
  Median latency: 559.3 ms
  P99 latency: 2195.6 ms
  Duration: 15.14 s
  All invariants: PASS

Run 2:
  999/1000 completed, 999 successful purchases, 1 failure
  Throughput: 71.4 req/s
  Median latency: 551.8 ms
  P99 latency: 1999.6 ms
  Duration: 14.00 s
  All invariants: PASS
Concurrency 60
Requests: 1000
Successful purchases: 1000
Failed requests: 0
Throughput: 57.4 req/s
Median latency: 887.6 ms
P99 latency: 2712.2 ms
Duration: 17.41 s
All invariants: PASS
Concurrency 75

The load test did not produce a normal final result — the final GET /status request timed out. No throughput or latency figures were recorded for this run and none are reported here.

Seller logs directly showed:

sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00

This is direct log evidence that the SQLAlchemy connection pool was exhausted at this concurrency level.

Findings
Concurrency 10 is a healthy operating point: median 74.2 ms, p99 461.2 ms, full throughput (104.3 req/s), zero failures.
Concurrency 25 already shows substantial latency growth, particularly at the tail: median roughly doubles to 147.8 ms, and p99 grows more than 2x to 1110.0 ms, even though throughput (100.3 req/s) and completion rate (1000/1000, zero failures) are essentially unchanged from concurrency 10. The tail is degrading well before throughput or correctness show any sign of trouble.
Concurrency 50 is a clear degradation region: median latency roughly quadruples again versus concurrency 25 (to ~550 ms across both runs), p99 reaches ~2.0-2.2 s, throughput drops to 66-71 req/s (down from ~100-104 req/s at lower concurrency), and each of the two independent runs recorded exactly one failed request out of 1000. Both runs are consistent with each other, which is evidence this is a reproducible degradation point rather than a one-off fluctuation, though the underlying cause of the single failure in each run was not captured in these metrics (no status code or error detail was recorded for it here).
Concurrency 60: correctness still holds fully (1000/1000 completed, all invariants PASS), but this is already a poor operating point in practice — median latency is 887.6 ms and p99 is 2712.2 ms (2.7 s), both worse than either concurrency-50 run. Throughput continues to fall (57.4 req/s).
Concurrency 75 is where the system breaks outright: the connection pool exhausts and requests can wait up to the 30-second pool timeout before failing, as shown directly by the sqlalchemy.exc.TimeoutError log line above. This is a hard failure mode, not a graceful degradation — no throughput/latency numbers could even be collected for this run because the load test's own final status check timed out.
Architectural context (inference, not measured in this investigation)

The following two facts about the system's design were established in prior code inspection, not by the runs above, and are included here only as candidate explanations for the trend, not as proven causes of these specific numbers:

Connection pool ceiling. The seller's SQLAlchemy engine is configured with the effective defaults pool_size=5 and max_overflow=10, giving a maximum of 15 concurrent database connections per seller process. The concurrency-75 TimeoutError is direct, measured confirmation that this ceiling was reached and exhausted at that load level. Whether pool queueing (rather than outright exhaustion) plausibly explains part of the latency growth already visible at concurrency 25-60 — where concurrency exceeds 15 but the pool hasn't yet been driven to a hard timeout — is a reasonable hypothesis, not something these runs directly measure.
Row-lock serialization. The ticket allocation path (find_next_available() in app/repository.py) always selects the single lowest-numbered AVAILABLE ticket via SELECT ... FOR UPDATE ... ORDER BY ticket_number ASC LIMIT 1. Every concurrent buyer therefore contends for the same one row at the database level, and PostgreSQL's row lock forces contenders to wait for that specific row rather than proceed in parallel. This is a structural property of the code, confirmed by inspection, and it is a plausible independent contributor to the latency trend above — the connection pool ceiling is not claimed to be the only bottleneck. Separating how much of the observed degradation comes from row-lock queueing versus connection-pool queueing was not done in this investigation and would require targeted measurement (e.g., holding connection availability effectively unconstrained while varying concurrency, or instrumenting lock wait time directly).
Conclusion

Based strictly on the measured results above, the practical saturation region for this system, as currently configured, is approximately concurrency 50-60: correctness holds throughout, but median and tail latency have already degraded substantially (into the several- hundred-millisecond to multi-second range) and throughput is visibly declining from its concurrency-10/25 baseline. Concurrency 75 produces a hard failure — measured, logged connection-pool exhaustion (QueuePool limit of size 5 overflow 10 reached, ... timeout 30.00) — rather than a graceful continuation of the degradation trend. Both the connection pool ceiling and the row-lock serialization in the ticket allocation path are plausible contributors to the pre-75 latency growth; this investigation does not isolate their relative contributions, and no code changes have been made based on these findings.