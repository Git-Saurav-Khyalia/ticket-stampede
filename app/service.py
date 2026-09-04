"""
Service layer: business logic.
 
============================================================================
VERSION HISTORY: WHAT VERSION 1 GOT WRONG, AND WHAT VERSION 2 FIXES
============================================================================
 
Version 1's buy_ticket() composed the sale from three separate,
unsynchronized round trips: count_sold() ("how many are sold so far?"),
find_next_available() ("which row can I sell?", no row lock), and
mark_sold() ("sell that row", no re-check at write time, no idempotency
check). Two concurrent requests could freely interleave these steps.
 
Two experiments proved two independent bugs, and it's worth being
precise about what each one actually demonstrated -- the original
docstring here overstated the first one.
 
Experiment 1 (5 tickets, 50 requests, concurrency 50, duplicate rate 0%):
"Successful response ticket uniqueness" FAILED. Ticket #2 was returned,
via a genuine HTTP 200, to 14 different requests in a single run. The
interleaving:
 
    Request A: count_sold()          -> 1  (well under 5, proceeds)
    Request B..N: count_sold()       -> 1  (same stale read, all proceed)
    Request A: find_next_available() -> ticket #2 (no lock taken)
    Request B..N: find_next_available() -> ticket #2 (SAME row, no lock)
    Request A: mark_sold(#2, user=A) ; commits
    Request B: mark_sold(#2, user=B) ; commits, overwriting A's row
    ... and so on for every other concurrent request that read ticket #2
 
Every one of those requests is told, correctly from its own point of
view, "you got ticket #2" -- because ticket_number is read once at
find_next_available() time and never changes; only the row's *other*
columns (user_id, request_id, sold_at) get overwritten by whichever
commit lands last. Because ticket rows are pre-created in bulk by
/reset -- find_next_available() can only ever SELECT an existing row,
it can never create one -- the *previous* version of this comment
claimed this could sell 101 tickets for a 100-ticket sale. That claim
does not hold for this architecture: the number of rows in the tickets
table for a sale is fixed at reset time and nothing in the naive /buy
flow can create a 101st row. What the race actually produces is
multiple buyers being told they hold the *same* one of the existing
rows, which is invisible to GET /status (the final row only ever shows
the last writer) but very visible by comparing the raw HTTP responses
against each other -- which is exactly why the load tester's
"Successful response ticket uniqueness" check exists as a check
separate from "Unique tickets" (the /status-based one).
 
Experiment 2 (20 tickets, 100 requests, concurrency 1, duplicate rate
50%): "Request idempotency" FAILED. This did NOT require any
concurrency at all -- it reproduces at concurrency 1, sequentially --
because nothing in Version 1 ever checked whether a request_id had
already been used. A retried request_id was simply sold a second,
brand-new ticket. This is a distinct bug from the race above: it is a
missing-dedup gap, not a read/write-ordering problem, and needed a
different fix (a database uniqueness constraint), not a lock.
 
============================================================================
VERSION 2: PostgreSQL is the sole concurrency authority
============================================================================
 
buy_ticket() below fixes both bugs with one transaction:
 
    BEGIN
        SELECT ... WHERE status = AVAILABLE ORDER BY ticket_number
            LIMIT 1 FOR UPDATE   -- find_next_available(): takes a row lock
        UPDATE that row SET status = SOLD, request_id = ...
                                 -- mark_sold(): staged, not yet committed
        COMMIT                   -- request_id uniqueness is checked HERE,
                                    by the (sale_id, request_id) constraint
                                    added in models.py
 
No Python lock, no application-level coordination, no reliance on
count_sold() (removed entirely -- see repository.py). Two mechanisms,
both enforced by PostgreSQL itself and therefore correct across any
number of seller processes sharing the database, not just within one
process:
 
  - FOR UPDATE fixes the Experiment 1 race: it serializes concurrent
    readers of the same AVAILABLE row so that only one of them can ever
    successfully claim it and commit; every other concurrent reader is
    forced to wait, then re-check, then move on to a *different*
    available row (or find none left).
 
  - The (sale_id, request_id) UNIQUE constraint fixes the Experiment 2
    bug: whichever transaction's COMMIT lands first for a given
    request_id wins; a losing transaction's COMMIT fails with
    IntegrityError, which is caught below, rolled back, and resolved by
    looking up and returning the ticket the winning transaction already
    committed. This is deliberately NOT implemented as ONLY a
    check-then-create (if request_exists(): return ... else: create
    ...) -- that has exactly the same race as Experiment 1, just on a
    different column, if it's the sole mechanism. The constraint, not a
    Python check, is the final authority.
 
buy_ticket() does include an upfront existence check as a fast path (a
plain, unlocked SELECT, before any row lock is taken) -- but this is
purely an optimization for the common case of a client retrying a
request_id that has already fully succeeded; it changes nothing about
correctness. It is stale the instant it returns, exactly like Version
1's count_sold() was, and could in principle miss a request_id that a
concurrent transaction is mid-commit on. That's fine, because it isn't
being relied on to catch that case -- the UNIQUE constraint and the
IntegrityError handler below still run on every request that reaches
them, and remain the only thing this design actually trusts to make
duplicate request_ids safe under concurrency. The fast path only ever
saves work (skipping a row lock and a doomed allocation attempt); it
never substitutes for the constraint.
 
One more race, found via a 1-ticket / 2-concurrent-requests /
same-request_id test: the fast-path miss (nothing found yet) and the
allocation attempt finding no AVAILABLE row are two separate reads, and
a concurrent request sharing the same request_id can commit the very
last ticket in the gap between them. Without a second look, that
manifests as a request being told SOLD_OUT for a request_id that, in
fact, already has a ticket -- an idempotency failure disguised as an
inventory failure. buy_ticket()'s "no AVAILABLE row" branch re-checks
(sale_id, request_id) once more before concluding SOLD_OUT, for exactly
this reason.
============================================================================
"""
from dataclasses import dataclass
 
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
 
from app.models import Sale, Ticket
from app.repository import SaleRepository, TicketRepository
 
 
class NoActiveSaleError(Exception):
    pass
 
 
class SoldOutError(Exception):
    pass
 
 
@dataclass
class BuyResult:
    ticket: Ticket
    sale: Sale
 
 
class SaleService:
    def __init__(self, db: Session):
        self.db = db
        self.sales = SaleRepository(db)
        self.tickets = TicketRepository(db)
 
    def reset(self, ticket_count: int) -> Sale:
        """
        Start a fresh sale. Per the spec, this is an administrative
        operation and is NOT supported concurrently with /buy -- callers
        are expected to only call /reset when no /buy traffic is in
        flight. We do not attempt to detect or guard against concurrent
        /buy calls here.
 
        Previous sale rows are preserved (not deleted); the previous sale
        is simply marked inactive so /status style queries for "the
        current sale" resolve unambiguously to the new one.
        """
        self.sales.deactivate_all()
        sale = self.sales.create_sale(ticket_total=ticket_count)
        self.tickets.bulk_create_tickets(sale.id, ticket_count)
        self.db.commit()
        self.db.refresh(sale)
        return sale
 
    def get_active_sale_or_raise(self) -> Sale:
        sale = self.sales.get_active_sale()
        if sale is None:
            raise NoActiveSaleError("No active sale. Call /reset first.")
        return sale
 
    def buy_ticket(self, user_id: str, request_id: str) -> BuyResult:
        """
        Version 2: transactionally safe allocation. See module docstring
        for the full account of what this replaces and why.
 
        Step order:
          1. Get the active sale.
          2. Fast path: look up (sale_id, request_id). If a ticket
             already exists for it, return that ticket immediately --
             no row lock, no allocation attempt, nothing written. This
             is an optimization only; see module docstring for why it
             cannot be relied on for correctness on its own.
          3. Otherwise, take the row lock (find_next_available's
             SELECT ... FOR UPDATE) and stage the write (mark_sold's
             UPDATE) in one transaction, with nothing committed in
             between -- PostgreSQL's row lock is what excludes every
             other concurrent request from claiming the same row while
             this transaction is still deciding.
          4. If there is no AVAILABLE row: roll back, then re-check
             (sale_id, request_id) with a fresh lookup before giving up.
             This matters specifically when the last ticket was just
             claimed by a concurrent request sharing this same
             request_id -- see the note on this branch below.
          5. Commit. If a concurrent transaction committed this exact
             request_id in the gap between step 2 and this commit, the
             (sale_id, request_id) UNIQUE constraint raises
             IntegrityError; roll back -- required before this session
             can be used again, and this also releases the row lock we
             took, returning that ticket to AVAILABLE for the next real
             buyer -- then look up and return the ticket the winning
             transaction already committed.
        """
        sale = self.get_active_sale_or_raise()
 
        # Fast path (step 2): a plain, unlocked read. Stale the instant
        # it returns -- see module docstring -- so it is never trusted
        # as the reason a duplicate is safe; it only ever saves work for
        # the common case where the request_id has already, genuinely,
        # been settled.
        existing = self.tickets.find_by_sale_and_request_id(sale.id, request_id)
        if existing is not None:
            return BuyResult(ticket=existing, sale=sale)
 
        ticket = self.tickets.find_next_available(sale.id)
        if ticket is None:
            # No AVAILABLE row right now -- but this can legitimately
            # happen because a CONCURRENT request sharing this exact
            # request_id just won the race for the last available
            # ticket and committed it, in the gap between our fast-path
            # check above (which found nothing) and this SELECT ... FOR
            # UPDATE (which found no AVAILABLE row because the last one
            # is now SOLD, committed by that other request). That is
            # not sold-out for THIS request_id -- it already has a
            # ticket, we just haven't looked again since it landed. Roll
            # back (this attempt took no lock worth holding, since
            # nothing matched, but ends this read cleanly) and check
            # once more before concluding this request_id is genuinely
            # unfulfilled.
            self.db.rollback()
            existing = self.tickets.find_by_sale_and_request_id(sale.id, request_id)
            if existing is not None:
                return BuyResult(ticket=existing, sale=sale)
            raise SoldOutError("No tickets remaining")
 
        self.tickets.mark_sold(ticket, user_id=user_id, request_id=request_id)
 
        try:
            self.db.commit()
        except IntegrityError:
            # Final authority (step 5): a concurrent transaction
            # committed this exact request_id after our fast-path check
            # above already ran (and found nothing) but before our own
            # commit landed. Roll back first: the session cannot be
            # used again until the failed transaction is explicitly
            # rolled back, and this also releases the row lock we took
            # above, returning that ticket to AVAILABLE for the next
            # real buyer.
            self.db.rollback()
            existing = self.tickets.find_by_sale_and_request_id(sale.id, request_id)
            if existing is None:
                # The constraint fired, so a row with this request_id
                # must exist; if it's somehow not found, something else
                # is wrong and this should not be silently swallowed.
                raise
            return BuyResult(ticket=existing, sale=sale)
 
        self.db.refresh(ticket)
        return BuyResult(ticket=ticket, sale=sale)
 
    def get_status(self, sale_id: str | None = None) -> tuple[Sale, list[Ticket]]:
        sale = self.sales.get_sale(sale_id) if sale_id else self.sales.get_active_sale()
        if sale is None:
            raise NoActiveSaleError("No active sale. Call /reset first.")
        sold_tickets = self.tickets.list_sold(sale.id)
        return sale, sold_tickets
 