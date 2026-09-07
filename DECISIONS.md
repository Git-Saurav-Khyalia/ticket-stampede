# Decisions

Ticket Stampede: a concurrent ticket-selling seller (FastAPI +
PostgreSQL + SQLAlchemy), a load-testing/verification client, and a
three-instance distributed deployment behind Nginx. Full experimental
evidence lives in `EXPERIMENTS.md` (correctness) and
`experiments/EXPERIMENTS.md` (performance); this document summarizes
and interprets rather than repeats that evidence.

## Architecture chosen, and what was rejected

**Chosen: PostgreSQL as the sole concurrency authority.** Ticket
allocation is `SELECT ... WHERE status='AVAILABLE' ORDER BY
ticket_number LIMIT 1 FOR UPDATE`, committed in the same transaction as
the `UPDATE` marking it `SOLD`. Idempotency is a `UNIQUE(sale_id,
request_id)` constraint; a losing concurrent duplicate's commit fails
with `IntegrityError`, and the service layer rolls back and returns the
ticket the winner already committed. No in-memory or process-level
lock exists anywhere. This wasn't just a constraint of the assignment
(no Redis/Kafka/app locks) — it's also what let the three-instance
deployment work with **zero application code changes**: correctness
depends on Postgres, not on which or how many processes handle a
request.

**Rejected: check-then-act idempotency** (`if exists: return else:
create`) — has the same race as naive ticket allocation, just on a
different column. The constraint, not a Python check, had to be the
final authority. (A fast, unlocked lookup is still used as an
optimization ahead of the locked path, but is explicitly never relied
on for correctness — see `app/service.py`.)

**Rejected: `SERIALIZABLE` isolation** — would need app-level retry
logic on serialization failures; `FOR UPDATE` at `READ COMMITTED` gives
the same guarantee here more simply, since access is always one
predictable row in a predictable order.

**Rejected: Nginx sticky sessions** — plain round-robin is correct
specifically because the app is stateless and Postgres is the only
source of truth.

## Trade-offs made under the time limit

The waitlist/reservation-expiry and kill-the-datastore-mid-sale stretch
goals were not attempted; time went instead into thoroughly proving the
required core and the three-instance deployment. The capacity
investigation deliberately stops at "plausible contributors, not
proven" for tail-latency degradation rather than fully isolating
connection-pool queueing from row-lock contention (see "What's next").
One anomalous distributed-capacity run (concurrency 75: 823/1000
completed, 177 failures, ~10s p99) did not reproduce on a later
identical run (182.5 req/s, 0 failures) — reported as evidence of
environmental variability rather than quietly dropped.

## How it was tested

- **Unit**: `load_test/tests/` — plan generation, metrics/percentiles,
  all five verifier invariants (pass and fail path each), synthetic data.
- **Sequential**: `tests/test_sequential.py` — reset, purchases
  (including repeated `request_id`), sold-out, `/status` accuracy.
- **Concurrent**: `tests/test_concurrency.py` — real thread-pool
  requests against real PostgreSQL: unique requests never collide,
  duplicate `request_id`s always resolve to one ticket, a regression
  test for a narrower race (below), and a mixed-traffic consistency check.
- **Naive-vs-fixed**: two deliberately-naive Version 1 experiments each
  proving one bug, then re-running the identical shape against Version 2
  confirming `Overall: PASS` (`EXPERIMENTS.md`).
- **Distributed correctness**: 1000 tickets, 2000 requests, concurrency
  50, duplicate rate 50%, seed 42, through Nginx — `Overall: PASS`.
  1984/2000 responses were successful `200`s, which is *not* 1984
  tickets sold — duplicates replay an already-issued ticket; final
  inventory was exactly 1000. `docker compose logs` showed all three
  instances handling `POST /buy` traffic (~1990-2000 lines each,
  cumulative — confirms the load balancer distributed traffic, not a
  precise per-experiment count).
- **Distributed capacity**: concurrency 10/25/50/75 through Nginx, 1000
  tickets/requests, duplicate rate 0 — full numbers in
  `experiments/EXPERIMENTS.md`.
- Full suite: **38 tests passing** at the latest verified checkpoint.

## Where it breaks

Tail latency degrades well before throughput or correctness show
trouble: in the single-instance investigation, p99 more than doubled
between concurrency 10 and 25 while throughput/completion were
unchanged. Measured saturation was ~concurrency 50-60; at 75 the
SQLAlchemy pool exhausted outright, logged directly as
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10
reached`. The distributed investigation did **not** reproduce a hard
failure at the levels tested, and did not observe `pg_locks` wait
events or direct pool-exhaustion evidence (`pg_stat_activity` showed
~15 idle `ClientRead` connections + 1 active — matching 3 instances ×
`pool_size=5` base connections each, a notable coincidence but not
proof of pool queueing on its own). **Row-lock contention was
investigated but not proven** — a deliberately evidence-scoped
conclusion, not a claim it's absent. Neither stretch goal has any
coverage.

## What's next, with two more weeks

1. **Isolate pool-queueing from row-lock contention**: re-run the same
   concurrency sweep with a drastically larger pool size; if tail
   latency improves sharply, that points at pooling, not locking.
2. **Waitlist/reservation-expiry** — the assignment flags this as where
   most naive solutions introduce their first new race; a good test of
   whether the database-authority approach generalizes.
3. **Kill-the-datastore-mid-sale** — verify safe degradation with no
   oversell or lost confirmed sales.
4. **Per-request correlation** (e.g. `application_name` tagging) so
   `pg_stat_activity` samples can be attributed to specific requests,
   sharpening item 1.
5. **Ticket-query cost as a sale progresses** — as low-numbered tickets
   sell out, `ORDER BY ticket_number LIMIT 1 FOR UPDATE` skips more
   already-SOLD rows; flagged as a hypothesis in
   `experiments/datastore_investigation.md`, not yet measured.

## A debugging note worth being direct about

Two things went wrong and are worth naming (full narrative: `logs/`).
First, a correct fix was applied to `load_test/models.py`, but the
person applying it was working from a stale local copy — the mismatch
surfaced as a confusing `TypeError`. It was resolved by directly
inspecting the actual imported module's file path and
`inspect.signature`, not by re-describing the fix. Second, a real bug
was found late: two concurrent requests sharing one `request_id`
racing for the *last* ticket could tell the loser `SOLD_OUT` even
though its `request_id` had just been fulfilled by the winner. Fixed
with a targeted re-check of `(sale_id, request_id)` in the
"no ticket available" branch before concluding the sale was genuinely
exhausted — a one-line addition, not a redesign.
