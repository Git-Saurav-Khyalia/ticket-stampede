# Ticket Stampede

A concurrency-safe ticket-selling service built to explore correctness under
high contention, idempotency, and multi-instance deployment.

## What this demonstrates

- Atomic ticket allocation under concurrent requests
- Database-enforced idempotency
- Verification of both final database state and HTTP responses
- Load testing with configurable concurrency and duplicate request replay
- Three seller instances behind Nginx with no application-level shared lock
- Measurement of throughput, median latency, and p99 latency
- Investigation of connection-pool contention and system saturation
- A deliberately unsafe implementation was built first and broken with the
  load tester before the correctness fix was introduced

## Architecture

```text
                    ┌───────────────┐
                    │ Load Tester   │
                    └───────┬───────┘
                            │
                            ▼
                     ┌─────────────┐
                     │    Nginx    │
                     │ Load Balancer│
                     └──────┬──────┘
                            │
                 ┌──────────┼──────────┐
                 ▼          ▼          ▼
             Seller-1   Seller-2   Seller-3
                 │          │          │
                 └──────────┼──────────┘
                            ▼
                       PostgreSQL


The seller application is stateless. All concurrency-sensitive state lives in
PostgreSQL.

The application does not use an in-memory lock, process-level lock, Redis
lock, sticky sessions, or another application-level coordinator.

This allows the same correctness model to work when requests are distributed
across multiple independent seller processes.

Application structure
app/
  main.py          FastAPI application entrypoint
  routes.py        HTTP layer
  service.py       Business logic and transaction boundaries
  repository.py    Database access
  models.py        SQLAlchemy models and constraints
  schemas.py       Pydantic request/response models
  config.py        Environment configuration
  database.py      SQLAlchemy engine/session setup

load_test/
  models.py        Load-test configuration and request/outcome models
  client.py        Concurrent HTTP client
  metrics.py       Throughput and latency metrics
  verifier.py      Correctness/invariant verification
  cli.py           Command-line interface
  tests/           Load-test unit tests

tests/
  conftest.py      PostgreSQL-backed test fixtures
  test_sequential.py
  test_concurrency.py

experiments/
  EXPERIMENTS.md   Performance and datastore investigation

nginx/
  nginx.conf       Load-balancer configuration

logs/
  README.md
  debugging-summary.md
  distributed-seller.log
  nginx-distributed.log
  <AI coding session transcripts>

DECISIONS.md       Architecture, trade-offs, limitations, and next steps
API
POST /reset

Starts a new sale with the requested number of tickets.

Request:

{
  "ticket_count": 100
}

A reset creates the ticket rows for the new sale and makes it the active
sale.

Example:

curl -X POST http://localhost:8000/reset \
  -H "Content-Type: application/json" \
  -d "{\"ticket_count\":100}"
POST /buy

Attempts to purchase a ticket.

Request:

{
  "user_id": "alice",
  "request_id": "request-123"
}

Example:

curl -X POST http://localhost:8000/buy \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"alice\",\"request_id\":\"request-123\"}"

If the same request_id is submitted again for the same sale, the request is
idempotent and returns the ticket associated with the original purchase.

If no tickets remain, the service returns a clear sold-out response.

GET /status

Returns the current sale, number of tickets sold, and the actual issued
tickets.

Example:

curl http://localhost:8000/status

The sold count is derived from the actual SOLD ticket rows rather than being
maintained as a separate counter.

Correctness strategy

The final implementation uses PostgreSQL as the concurrency authority.

Ticket allocation

The seller selects the lowest-numbered available ticket using a row-level
lock:

SELECT ...
FROM tickets
WHERE sale_id = ...
  AND status = 'AVAILABLE'
ORDER BY ticket_number
LIMIT 1
FOR UPDATE;

The same transaction then marks that ticket as SOLD.

This prevents two concurrent transactions from successfully claiming the same
ticket row.

Idempotency

The database enforces:

UNIQUE(sale_id, request_id)

The service performs a fast lookup first, but that lookup is only an
optimization.

The database uniqueness constraint is the final correctness authority when
two identical request IDs race concurrently.

If one transaction wins and another encounters the uniqueness violation, the
losing request rolls back and retrieves the already-committed purchase.

Status consistency

/status derives the sold count and issued-ticket list from the actual ticket
rows.

This avoids maintaining a separate counter that could diverge from the
tickets actually marked SOLD.

Why the verifier checks HTTP responses

A key finding from the naive implementation was that final database state
alone is not sufficient to verify the ticket-issuance contract.

Because tickets are pre-created rows, two concurrent requests in the naive
implementation could both read the same available ticket and both return
that ticket number to buyers.

The final database could still contain only one SOLD row.

Therefore:

Final database state:
    ticket #2 -> SOLD

Observed HTTP responses:
    request A -> ticket #2
    request B -> ticket #2

The database appears internally consistent, but the service has incorrectly
issued the same ticket twice.

The load verifier therefore checks both:

the final /status state; and
the raw successful HTTP responses, correlated with request IDs.

This additional check catches a correctness failure that a final-state-only
verifier would miss.

Load testing

The load client uses asynchronous HTTP requests with bounded concurrency.

It supports:

configurable ticket count
configurable request count
configurable concurrency
duplicate request ID replay
deterministic duplicate selection through a seed
configurable request timeout
automatic reset unless disabled
invariant verification
throughput calculation
median latency
p99 latency

Example:

python -m load_test.cli \
  --seller-url http://localhost:8000 \
  --tickets 1000 \
  --requests 1000 \
  --concurrency 50 \
  --duplicate-rate 0 \
  --seed 42

The output reports:

Requests completed
Successful purchases
Failed requests
Requests per second
Median latency
P99 latency

No overselling: PASS/FAIL
Unique tickets: PASS/FAIL
Request idempotency: PASS/FAIL
Status consistency: PASS/FAIL
Successful response ticket uniqueness: PASS/FAIL

Overall: PASS/FAIL
Naive implementation

Version 1 was intentionally implemented using an unsafe read-then-write
approach.

The allocation process consisted of separate database operations:

read state
    |
find available ticket
    |
mark ticket SOLD

There was no row-level lock and no database idempotency constraint.

The load tester was then used to demonstrate that the implementation was
unsafe.

Naive concurrency failure

Under concurrent requests, multiple successful HTTP responses could return
the same ticket number.

The final database did not necessarily show an oversold count because the
tickets were pre-created rows. The externally observed issuance contract was
nevertheless violated.

Naive idempotency failure

With repeated request IDs, the naive implementation could issue a new ticket
for the same logical request.

This demonstrated that idempotency could not safely be implemented as a
simple application-level check.

The detailed failing experiments and corrected runs are documented in
EXPERIMENTS.md.

Distributed deployment

The final system runs three independent seller instances behind Nginx:

Nginx
  |
  +-- Seller 1
  |
  +-- Seller 2
  |
  +-- Seller 3
       |
       v
   PostgreSQL

The seller processes do not share application memory.

There is deliberately no single application-level lock.

Instead, every seller instance uses PostgreSQL transactions and constraints
as the shared correctness boundary.

Distributed correctness test

The distributed system was tested with:

Tickets:        1000
Requests:       2000
Concurrency:    50
Duplicate rate: 50%
Seed:           42

The run completed with:

2,000 requests processed
1,984 successful HTTP responses
16 failed responses
all correctness invariants passing
final inventory consistent with exactly 1,000 tickets sold

The 1,984 successful HTTP responses do not mean 1,984 tickets were sold.
Duplicate request IDs intentionally replay successful purchases, so multiple
HTTP responses can refer to the same already-issued ticket.

Nginx logs also confirmed that all three seller instances received
POST /buy traffic.

Performance investigation

The distributed deployment was tested with:

1000 tickets
1000 requests
duplicate rate 0
seed 42

Results:

Concurrency	Throughput	Median	P99	Failures
10	48.6 req/s	75.1 ms	3197.2 ms	0
25	100.1 req/s	76.6 ms	8923.7 ms	2
50	145.5 req/s	74.7 ms	6365.4 ms	0
75	182.5 req/s	120.7 ms	5052.8 ms	0

The main performance observation is that median latency remained relatively
low while p99 latency became several seconds.

The tail latency was therefore much more volatile than the median.

An earlier concurrency-75 run produced 177 failures and approximately 10
seconds p99 latency, but an identical later run completed successfully.
That earlier result is retained as evidence of environmental variability,
not presented as a deterministic capacity limit.

Bottleneck investigation

Several possible causes were considered:

PostgreSQL query execution time
SQLAlchemy connection-pool queueing
PostgreSQL row-lock contention
HTTP/client-side timeout behavior
ticket-selection query cost as the sale progresses

PostgreSQL activity was sampled during distributed load.

The observed state included approximately:

15 idle ClientRead connections
1 active connection

No PostgreSQL Lock wait events were observed during the sampled runs.

The three seller instances use SQLAlchemy's default base pool size of five
connections each, giving fifteen persistent connections collectively.

A separate single-instance run also directly demonstrated SQLAlchemy
QueuePool exhaustion under sufficiently high concurrency.

The conclusion is deliberately evidence-scoped: severe tail latency is real,
but the distributed measurements did not isolate one single bottleneck with
enough evidence to claim that PostgreSQL locking or pool exhaustion alone is
the definitive cause.

Further investigation is documented in
experiments/EXPERIMENTS.md.

Tests

The project uses a real PostgreSQL database for the application tests because
the important correctness guarantees depend on real database transactions,
row locks, and constraints.

Run:

docker compose up -d db
pip install -r requirements.txt
pytest

The test suite covers:

reset behavior
sequential purchases
repeated request IDs
sold-out behavior
status accuracy
sale history
concurrent unique requests
concurrent duplicate request IDs
mixed unique/duplicate traffic
regression coverage for the one-ticket duplicate-request race

The latest verified test suite contains:

39 passed

The load tester also has its own unit tests under:

load_test/tests/
Running the complete distributed system

Requirements:

Docker Desktop
Python 3.13+

Start the stack:

docker compose up --build -d

Check the containers:

docker compose ps

The complete system is available through Nginx at:

http://localhost:8000

FastAPI interactive documentation:

http://localhost:8000/docs

Reset a sale:

curl -X POST http://localhost:8000/reset \
  -H "Content-Type: application/json" \
  -d "{\"ticket_count\":100}"

Check status:

curl http://localhost:8000/status

Run the load tester:

python -m load_test.cli \
  --seller-url http://localhost:8000 \
  --tickets 1000 \
  --requests 1000 \
  --concurrency 50 \
  --duplicate-rate 0 \
  --seed 42
Clean-checkout workflow

From a clean checkout:

docker compose up --build -d
pip install -r requirements.txt
pytest

The API can then be accessed through:

http://localhost:8000

For a clean environment, configuration is supplied through .env using
.env.example as the template.

Engineering decisions

The main architectural decisions are documented in:

DECISIONS.md

Important rejected alternatives include:

application-level locks
Redis/distributed locks
sticky sessions
check-then-act idempotency
SERIALIZABLE isolation

The primary design principle is that correctness should live in the shared
datastore rather than in process-local memory.

Known limitations

This project intentionally focuses on the core concurrency problem and the
three-instance distributed extension.

Known limitations include:

/reset is an administrative operation and is not designed to race
concurrently with /buy.
Ticket allocation always searches for the lowest-numbered available ticket.
High concurrency can produce substantial tail latency.
The exact source of distributed tail-latency degradation was not fully
isolated within the time budget.
The datastore-slow-for-ten-seconds experiment was not fully completed.
The waitlist/reservation-expiry extension was not implemented.
Datastore failure/recovery during an active sale was not fully tested.
The buyer itself is not distributed across multiple load-generator
processes.

These were consciously left as future work rather than implemented
partially.

What I would do with two more weeks
Isolate connection-pool queueing from database row-lock contention by
repeating the concurrency sweep with a substantially larger pool.
Measure the allocation query with EXPLAIN ANALYZE early and late in a
sale to determine whether scanning past sold tickets becomes significant.
Add request correlation/application metadata to PostgreSQL activity
sampling.
Test datastore failure and recovery during an active sale.
Implement and race-test reservation expiry and a waitlist state machine.
Run the load generator across multiple processes to separate seller
capacity from client-side limitations.
AI-assisted development

AI coding tools were used during development.

The full coding-session transcripts are included under:

logs/

The sessions document how the implementation was directed, tested, corrected,
and reviewed.

Two debugging incidents were particularly important:

An AI-generated change was initially applied to the wrong models.py.
The mistake was caught by checking the actual import path and function
signature before accepting the change.
The first concurrency fix did not fully handle a narrow race involving two
concurrent requests with the same request_id when only one ticket
remained. A regression test exposed the problem, leading to an additional
request-id recheck after the locked allocation path.

These incidents reinforced the principle that AI-generated code was treated
as a proposal to verify rather than as an authority.

Evidence

Detailed experimental results are available in:

EXPERIMENTS.md
experiments/EXPERIMENTS.md

AI development evidence is available in:

logs/

Architectural reasoning and trade-offs are available in:

DECISIONS.md
```
