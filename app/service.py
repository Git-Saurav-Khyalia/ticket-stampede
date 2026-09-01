"""
Service layer: business logic.

============================================================================
VERSION 1 -- NAIVE -- CONTAINS AN INTENTIONAL, KNOWN RACE CONDITION
============================================================================

buy_ticket() below implements the sale as three separate, unsynchronized
steps against the database:

    1. count_sold(sale_id)               -- "how many are sold so far?"
    2. find_next_available(sale_id)      -- "which row can I sell?"
    3. mark_sold(ticket, ...)            -- "sell that row"

Each step is its own round trip. Nothing prevents two concurrent requests
from interleaving these steps, e.g.:

    Request A: count_sold()      -> 99         (thinks: 1 ticket left, ok)
    Request B: count_sold()      -> 99         (thinks: 1 ticket left, ok)
    Request A: find_next_available() -> ticket #100
    Request B: find_next_available() -> ticket #100   (SAME row -- no lock)
    Request A: mark_sold(#100, user=alice)
    Request B: mark_sold(#100, user=bob)               (overwrites A's sale)

Both requests are told they bought ticket #100. /status will show only
Bob (the last write wins), meaning Alice paid (in a real system) for a
ticket she does not have, and the total ticket count invariant is not
violated in this exact interleaving -- but a *worse* interleaving lets
count_sold() be stale for BOTH requests when only 1 ticket remains,
letting both proceed past the "is there a ticket left" check entirely
and both grab distinct available rows, selling 101 tickets for a
100-ticket sale. That is the oversell bug.

There is a second, independent bug: nothing here checks whether
request_id has been seen before. A client that retries the same
request_id (e.g. after a timed-out response it never saw) will be sold
a second, brand-new ticket. This is unrelated to the race condition
above -- it would happen even with only ONE request at a time, run
twice sequentially -- and is fixed differently in Version 2 (a unique
constraint + conflict handling), not by locking.

DO NOT "fix" this file. That is the job of Version 2.
============================================================================
"""
from dataclasses import dataclass

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
        NAIVE, INTENTIONALLY UNSAFE implementation. See module docstring.

        Three unsynchronized steps: count -> find -> mark. No row lock,
        no SELECT ... FOR UPDATE, no SERIALIZABLE isolation, no
        ON CONFLICT / unique-constraint-based idempotency, no
        application-level lock. Two concurrent calls can race each
        other at every step.
        """
        sale = self.get_active_sale_or_raise()

        # Step 1: how many are sold right now? (stale the instant it returns)
        sold_count = self.tickets.count_sold(sale.id)
        if sold_count >= sale.ticket_total:
            raise SoldOutError("No tickets remaining")

        # Step 2: which ticket looks available? (no lock taken on the row)
        ticket = self.tickets.find_next_available(sale.id)
        if ticket is None:
            # Can happen even though sold_count < ticket_total, if a
            # concurrent request already grabbed the last available row
            # between step 1 and step 2 -- itself a symptom of the race.
            raise SoldOutError("No tickets remaining")

        # Step 3: mark it sold. No re-check that it's still AVAILABLE at
        # write time, no idempotency check on request_id.
        self.tickets.mark_sold(ticket, user_id=user_id, request_id=request_id)
        self.db.commit()
        self.db.refresh(ticket)

        return BuyResult(ticket=ticket, sale=sale)

    def get_status(self, sale_id: str | None = None) -> tuple[Sale, list[Ticket]]:
        sale = self.sales.get_sale(sale_id) if sale_id else self.sales.get_active_sale()
        if sale is None:
            raise NoActiveSaleError("No active sale. Call /reset first.")
        sold_tickets = self.tickets.list_sold(sale.id)
        return sale, sold_tickets
