"""
Repository layer: raw data access only. No business rules live here.
 
============================================================================
VERSION HISTORY
============================================================================
Version 1 composed the /buy flow from three unsynchronized round trips:
count_sold() (a plain COUNT), find_next_available() (a plain SELECT with
no lock), and mark_sold() (a plain UPDATE with no re-check). Experiment 1
(5 tickets, 50 requests, concurrency 50, duplicate rate 0%) proved the
consequence directly: ticket #2 was returned successfully to 14 different
requests in one run. Because tickets are pre-created in bulk by /reset --
find_next_available() can only ever SELECT an existing row, never create
one -- this could not oversell past ticket_total (the row count is fixed
at reset time), but it could and did let the same row be read as
AVAILABLE by many concurrent callers before any of their writes had
committed, and mark_sold()'s unconditional UPDATE let every one of them
succeed, each overwriting the last. The final row only ever shows the
last writer, which is exactly why GET /status could not detect this --
see app/service.py for the full account.
 
Version 2 replaces that with a single safe DB-authoritative operation:
find_next_available() now takes a row lock (SELECT ... FOR UPDATE), and
mark_sold() is only ever committed in the same transaction as that lock,
with the (sale_id, request_id) UNIQUE constraint (see models.py) as the
final authority on duplicate request_ids. count_sold() is removed
entirely -- Experiment 1 is exactly why it must never be the correctness
mechanism; the actual row state, protected by FOR UPDATE, is authoritative
now. See app/service.py buy_ticket() for the full transaction.
============================================================================
"""
from datetime import datetime, timezone
 
from sqlalchemy import select, update
from sqlalchemy.orm import Session
 
from app.models import Sale, Ticket, TicketStatus
 
 
class SaleRepository:
    def __init__(self, db: Session):
        self.db = db
 
    def deactivate_all(self) -> None:
        self.db.execute(update(Sale).values(active=False))
 
    def create_sale(self, ticket_total: int) -> Sale:
        sale = Sale(ticket_total=ticket_total, active=True)
        self.db.add(sale)
        self.db.flush()  # populate sale.id
        return sale
 
    def get_active_sale(self) -> Sale | None:
        stmt = select(Sale).where(Sale.active.is_(True)).order_by(Sale.created_at.desc())
        return self.db.execute(stmt).scalars().first()
 
    def get_sale(self, sale_id: str) -> Sale | None:
        return self.db.get(Sale, sale_id)
 
 
class TicketRepository:
    def __init__(self, db: Session):
        self.db = db
 
    def bulk_create_tickets(self, sale_id: str, ticket_total: int) -> None:
        tickets = [
            Ticket(sale_id=sale_id, ticket_number=n, status=TicketStatus.AVAILABLE)
            for n in range(1, ticket_total + 1)
        ]
        self.db.add_all(tickets)
 
    def find_next_available(self, sale_id: str) -> Ticket | None:
        """
        Selects the lowest-numbered AVAILABLE ticket for this sale and
        takes a row-level lock on it (SELECT ... FOR UPDATE) as part of
        the caller's transaction. Any other concurrent transaction that
        tries to select this same row FOR UPDATE will block until this
        transaction commits or rolls back; once it commits, the row's
        status is no longer AVAILABLE, so a blocked concurrent caller's
        query re-evaluates its WHERE clause against the new committed
        state and moves on to the next AVAILABLE row instead of also
        returning this one. That is what prevents the Version 1 failure
        (the same row being read as AVAILABLE by many concurrent callers
        at once) -- see service.py buy_ticket() for how this is used
        inside one transaction together with mark_sold().
        """
        stmt = (
            select(Ticket)
            .where(Ticket.sale_id == sale_id, Ticket.status == TicketStatus.AVAILABLE)
            .order_by(Ticket.ticket_number.asc())
            .limit(1)
            .with_for_update()
        )
        return self.db.execute(stmt).scalars().first()
 
    def mark_sold(self, ticket: Ticket, user_id: str, request_id: str) -> None:
        """
        Marks the given ticket (already row-locked by find_next_available
        within the same transaction) as SOLD, together with the buyer's
        user_id and request_id. This is only ever committed together
        with that lock, in the same transaction -- see service.py
        buy_ticket(). The (sale_id, request_id) UNIQUE constraint
        (models.py) is what actually enforces idempotency; this method
        just stages the write. If the request_id has already been used
        for this sale, the eventual commit raises IntegrityError, which
        the service layer handles by rolling back and returning the
        original ticket instead.
        """
        ticket.status = TicketStatus.SOLD
        ticket.user_id = user_id
        ticket.request_id = request_id
        ticket.sold_at = datetime.now(timezone.utc)
        self.db.add(ticket)
 
    def list_sold(self, sale_id: str) -> list[Ticket]:
        stmt = (
            select(Ticket)
            .where(Ticket.sale_id == sale_id, Ticket.status == TicketStatus.SOLD)
            .order_by(Ticket.ticket_number.asc())
        )
        return list(self.db.execute(stmt).scalars().all())
 
    def find_by_sale_and_request_id(self, sale_id: str, request_id: str) -> Ticket | None:
        """
        Looks up the single ticket already associated with this
        (sale_id, request_id), if any. Used by the service layer's
        IntegrityError handler after a duplicate request_id loses the
        race for a ticket: since uq_sale_request_id (models.py)
        guarantees at most one row can exist for a given (sale_id,
        request_id), this lookup is unambiguous -- there is exactly one
        ticket to return, not several to disambiguate between.
        """
        stmt = select(Ticket).where(
            Ticket.sale_id == sale_id, Ticket.request_id == request_id
        )
        return self.db.execute(stmt).scalars().first()
 