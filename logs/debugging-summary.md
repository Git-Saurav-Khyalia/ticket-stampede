# Debugging summary

A short, honest account of two moments worth naming directly. This is a
summary written after the fact, not a raw transcript — see `README.md`
in this directory for why the raw transcript isn't here yet.

## 1. A correct fix, applied to a stale local file

A fix to `load_test/models.py` — making `request_id` generation
deterministic from a seeded RNG instead of `uuid.uuid4()`, so that
`build_request_plan(config, rng)` would be reproducible for a given
seed — was written correctly. The person applying it, however, was
working from a local copy of the file that predated the fix. Running
the test suite against that stale copy produced a confusing
`TypeError: new_request_id() takes 0 positional arguments but 1 was
given`, which looked at first like the fix had never actually been
made.

Re-describing the fix, or re-explaining what the code was supposed to
do, would not have resolved this — the actual file on disk was simply
different from the file being discussed. What resolved it was checking
the **live, imported module** directly:

```python
import load_test.models as m
import inspect
print(m.__file__)
print(inspect.signature(m.new_request_id))
```

This showed the on-disk file's real path and its real function
signature, which made the mismatch between "what the fix says" and
"what the running code actually has" unambiguous, and pointed
straight at "replace this specific file with the current version"
rather than at any further code change. The lesson generalizes: when a
described fix and observed behavior disagree, checking the actual
imported artifact is more reliable than re-checking the description of
the fix.

## 2. A real race found late: SOLD_OUT for a request that had already succeeded

After the core Version 2 fix (row-level `SELECT ... FOR UPDATE` for
allocation, a `UNIQUE(sale_id, request_id)` constraint for idempotency)
was in place and passing its initial concurrent tests, a narrower
scenario was tested directly: **one ticket, two concurrent requests,
same `request_id`**.

The failure mode: both requests could miss the fast-path idempotency
lookup (neither request_id row existed yet). One acquires the row lock,
sells the ticket, and commits. The other, having been blocked waiting
for that same lock, wakes up to find **no `AVAILABLE` ticket at all**
(the only one is now `SOLD`) — and the code at that point simply raised
`SOLD_OUT`, without checking whether its own `request_id` had, in fact,
just been fulfilled by the winner.

This is a real bug, not a flaky test: a client retrying a request that
had already succeeded could be told the sale was sold out, when it
already held a ticket. The fix was narrow and targeted, not a
redesign: in the "no ticket available" branch specifically, roll back
and perform one more lookup for `(sale_id, request_id)` before
concluding the request is genuinely unfulfilled. If that lookup finds a
ticket, return it; only if it doesn't is the request actually sold out.
A regression test for exactly this scenario (1 ticket, 2 concurrent
requests, 1 shared `request_id`, both expected to receive ticket #1)
was added alongside the fix.
