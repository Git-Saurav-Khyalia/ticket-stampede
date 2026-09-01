"""
Repository layer: raw data access only. No business rules live here.

Version 1 (Naive) note:
Every method here does exactly one simple thing and returns. The unsafe
sequence of "count, then find-one, then update" is composed in service.py,
not here -- that composition, across multiple round trips to the database
with no lock and no atomic statement tying them together, is the race
condition. See service.py for the full explanation.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select, update
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

    def count_sold(self, sale_id: str) -> int:
        """Step 1 of the naive /buy flow. A plain COUNT, no locking."""
        stmt = select(func.count()).where(
            Ticket.sale_id == sale_id, Ticket.status == TicketStatus.SOLD
        )
        return self.db.execute(stmt).scalar_one()

    def find_next_available(self, sale_id: str) -> Ticket | None:
        """
        Step 2 of the naive /buy flow. An ordinary SELECT with no
        row-level locking (no SELECT ... FOR UPDATE). Two concurrent
        callers can both get back the SAME row here.
        """
        stmt = (
            select(Ticket)
            .where(Ticket.sale_id == sale_id, Ticket.status == TicketStatus.AVAILABLE)
            .order_by(Ticket.ticket_number.asc())
            .limit(1)
        )
        return self.db.execute(stmt).scalars().first()

    def mark_sold(self, ticket: Ticket, user_id: str, request_id: str) -> None:
        """
        Step 3 of the naive /buy flow. An ordinary UPDATE of the row that
        was read back in find_next_available. There is no WHERE clause
        re-checking that the row is still AVAILABLE at write time, and no
        conditional/optimistic check on the read value -- so two callers
        that both read the same AVAILABLE row will both successfully
        UPDATE it to SOLD, one silently overwriting the other's sale.
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

    def find_by_request_id(self, sale_id: str, request_id: str) -> list[Ticket]:
        """
        Used only for observability / tests, NOT for idempotency
        protection. In Version 1 this can legitimately return more than
        one row for the same request_id -- that duplication is the bug
        under test.
        """
        stmt = select(Ticket).where(
            Ticket.sale_id == sale_id, Ticket.request_id == request_id
        )
        return list(self.db.execute(stmt).scalars().all())
