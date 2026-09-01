"""
SQLAlchemy models.

Version 1 (Naive) note:
There is deliberately NO unique constraint on (sale_id, request_id) here.
Duplicate request_ids are allowed to create two separate rows / two separate
tickets for the same logical purchase. That is intentional -- it is the
second bug this version is meant to expose. It gets added in Version 2.

Ticket numbers ARE unique within a sale (enforced by a unique constraint),
because ticket rows are pre-created in bulk during /reset rather than
assigned one-at-a-time during /buy. Assigning a *pre-created* row to a
buyer is exactly where the read-then-write race lives -- see service.py.
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus), default=TicketStatus.AVAILABLE, nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # NOTE: intentionally NOT unique in Version 1. See module docstring.
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sale: Mapped["Sale"] = relationship(back_populates="tickets")
