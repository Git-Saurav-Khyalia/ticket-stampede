from datetime import datetime

from pydantic import BaseModel, Field


class ResetRequest(BaseModel):
    ticket_count: int = Field(gt=0, description="Number of tickets to create for the new sale")


class ResetResponse(BaseModel):
    sale_id: str
    ticket_total: int


class BuyRequest(BaseModel):
    user_id: str
    request_id: str


class BuyResponse(BaseModel):
    ticket_number: int
    sale_id: str


class SoldOutResponse(BaseModel):
    detail: str = "SOLD_OUT"


class SoldTicket(BaseModel):
    ticket_number: int
    user_id: str | None
    sold_at: datetime | None


class StatusResponse(BaseModel):
    sale_id: str
    ticket_total: int
    sold_count: int
    sold_tickets: list[SoldTicket]
