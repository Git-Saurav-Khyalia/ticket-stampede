"""
SQLAlchemy models.
 
============================================================================
VERSION HISTORY
============================================================================
Version 1 (naive) had NO unique constraint on (sale_id, request_id).
Experiment 2 (20 tickets, 100 requests, concurrency 1, duplicate rate 50%)
proved this let a single repeated request_id create two separate ticket
allocations for what should have been one logical purchase -- observed
directly as "Request idempotency: FAIL" (same request_id producing
multiple successful purchases), even at concurrency 1. That bug did not
require any race at all; it was a pure missing-dedup gap.
 
Version 2 adds the UniqueConstraint below and makes it the sole
authority for idempotency (see app/service.py buy_ticket for how a
constraint violation is handled). request_id remains nullable because
AVAILABLE tickets (pre-created by /reset, not yet purchased) have
request_id = NULL; a standard SQL UNIQUE constraint treats every NULL
as distinct from every other NULL, so any number of AVAILABLE tickets
with request_id = NULL can coexist for one sale without violating this
constraint. The constraint only starts mattering once a real, non-NULL
request_id is written -- exactly the case it needs to guard.
============================================================================
 
Ticket numbers ARE unique within a sale (enforced by a unique constraint),
because ticket rows are pre-created in bulk during /reset rather than
assigned one-at-a-time during /buy. See app/repository.py and
app/service.py for how Version 2 safely assigns one of these
pre-created rows to a buyer using PostgreSQL row-level locking.
"""
import enum
import uuid
from datetime import datetime, timezone
 
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
 
from app.database import Base
 
 
def utcnow() -> datetime:
    return datetime.now(timezone.utc)
 
 
class TicketStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    SOLD = "SOLD"
 
 
class Sale(Base):
    __tablename__ = "sales"
 
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ticket_total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
 
    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )
 
 
class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # Ticket numbers are unique within a sale. This constraint is safe
        # to have here because tickets are pre-created by /reset, not
        # created-on-demand by /buy. /buy only ever UPDATEs an existing row.
        UniqueConstraint("sale_id", "ticket_number", name="uq_sale_ticket_number"),
        # Version 2: the idempotency authority. A given request_id can be
        # associated with at most one ticket row per sale. NULLs (the
        # AVAILABLE tickets' default) are exempt from this uniqueness
        # check under standard SQL semantics -- see module docstring.
        UniqueConstraint("sale_id", "request_id", name="uq_sale_request_id"),
    )
 
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus), default=TicketStatus.AVAILABLE, nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Version 2: enforced unique per sale via uq_sale_request_id above.
    # NULL for any ticket still AVAILABLE; set exactly once, at the same
    # commit that marks the ticket SOLD, for any ticket that has been
    # purchased.
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
 
    sale: Mapped["Sale"] = relationship(back_populates="tickets")
 