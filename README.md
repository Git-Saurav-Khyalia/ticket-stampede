# Ticket Stampede — Seller

## ⚠️ VERSION 1: NAIVE — INTENTIONALLY UNSAFE UNDER CONCURRENCY

This is the **first** of at least two versions of this service. It
implements the required endpoints (`/reset`, `/buy`, `/status`) with the
**obvious, unprotected** read-then-write logic on purpose, so that a
concurrent load test can demonstrate real failures before they are fixed.

**Do not use this version as a reference for correct behavior.** It is
correct only when calls are made one at a time. Two bugs are present by
design:

1. **A race condition in `/buy`.** Ticket count is read, an available
   ticket is found, and that ticket is marked sold, as three separate,
   unsynchronized database round trips. Concurrent requests can interleave
   these steps and both be sold the same ticket, or oversell past the
   ticket total. Full explanation with an interleaving diagram: see the
   module docstring at the top of `app/service.py`.
2. **No idempotency protection on `request_id`.** Replaying the same
   `request_id` — even sequentially, not just concurrently — issues a
   second ticket. There is no unique constraint on `request_id` yet. This
   is deliberate; see `app/models.py`.

Both bugs are fixed in Version 2, which adds the constraints and atomic
operations described in the architecture review, not by adding locks
around this same code.

## What's here vs. what's not

| Included in Version 1 | Not yet (comes later) |
|---|---|
| `/reset`, `/buy`, `/status` endpoints | Load testing client |
| Sequential-only pytest suite | Concurrency protection of any kind |
| Docker Compose (Postgres + app) | Nginx / multi-instance deployment |
| | Idempotency enforcement |

## Architecture

```
app/
  main.py        FastAPI app entrypoint, table creation on startup
  routes.py       HTTP layer — request/response only, no business logic
  service.py      Business logic — THE RACE CONDITION LIVES HERE
  repository.py   Raw DB access — one simple operation per method
  models.py       SQLAlchemy models (Sale, Ticket)
  schemas.py      Pydantic request/response models
  config.py       Environment-based settings
  database.py     Engine / session setup
tests/
  conftest.py         Test fixtures (Postgres-backed, truncates between tests)
  test_sequential.py  Sequential-only behavior tests (see note below)
```

Each layer has one job: routes translate HTTP ↔ Python, service holds the
business rules (and, in this version, the bug), repository issues plain
SQL via SQLAlchemy, models define the schema.

## Data model

**Sale**: `id`, `ticket_total`, `created_at`, `active`
**Ticket**: `id`, `sale_id` (FK), `ticket_number`, `status`, `user_id`,
`request_id`, `sold_at`

- Ticket rows are **pre-created in bulk** by `/reset` (one row per ticket
  number, `AVAILABLE`), not created on demand by `/buy`. `/buy` only ever
  updates an existing row's status. This is why `ticket_number` can safely
  carry a uniqueness constraint in this version even though `request_id`
  cannot yet — the constraint on `ticket_number` is enforced once, at
  bulk-insert time, not raced against.
- `request_id` has **no unique constraint** in this version, by design.
- Previous sales are **not deleted** on `/reset` — the old `Sale` row is
  marked `active = false` and a new one is created. `/status` and `/buy`
  always resolve against the current active sale.
- Per spec, `/reset` is documented as an **administrative operation not
  supported concurrently with `/buy`** — no attempt is made here to guard
  against that combination.

## Running it

Requires Docker and Docker Compose.

```bash
docker compose up --build
```

This starts Postgres and the seller API. The API will be available at
`http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

### Example requests

```bash
curl -X POST localhost:8000/reset -H 'Content-Type: application/json' \
  -d '{"ticket_count": 100}'

curl -X POST localhost:8000/buy -H 'Content-Type: application/json' \
  -d '{"user_id": "alice", "request_id": "r-1"}'

curl localhost:8000/status
```

### Running tests

Tests need a running Postgres instance (they run against real Postgres,
not a mock — the constraints and behavior being tested are DB-dependent).

```bash
docker compose up -d db
pip install -r requirements.txt
pytest
```

Tests cover **sequential behavior only**, as specified for this version:
reset, single and repeated sequential purchases, sold-out, `/status`
accuracy, and sale history preservation across `/reset`. They also include
one test (`test_duplicate_request_id_run_sequentially_is_KNOWN_to_be_unsafe`)
that documents — rather than hides — the known `request_id` gap.
Concurrent behavior is intentionally **not** tested here; that's the job
of the load tester in the next step, which is expected to make this
version fail.

## What must remain wrong until Version 2

Do not add any of the following to this version — they will be introduced
deliberately in Version 2, with a load-test run demonstrating the failure
first:

- Row-level locking (`SELECT ... FOR UPDATE`)
- `SERIALIZABLE` isolation
- `ON CONFLICT` / unique-constraint-based idempotency handling on `request_id`
- Any `asyncio`/`threading` application-level lock
- Redis or any external coordinator
