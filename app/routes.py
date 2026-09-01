from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    BuyRequest,
    BuyResponse,
    ResetRequest,
    ResetResponse,
    SoldTicket,
    StatusResponse,
)
from app.service import NoActiveSaleError, SaleService, SoldOutError

router = APIRouter()


@router.post("/reset", response_model=ResetResponse)
def reset(payload: ResetRequest, db: Session = Depends(get_db)):
    service = SaleService(db)
    sale = service.reset(ticket_count=payload.ticket_count)
    return ResetResponse(sale_id=sale.id, ticket_total=sale.ticket_total)


@router.post(
    "/buy",
    response_model=BuyResponse,
    responses={409: {"description": "Sold out"}, 404: {"description": "No active sale"}},
)
def buy(payload: BuyRequest, db: Session = Depends(get_db)):
    service = SaleService(db)
    try:
        result = service.buy_ticket(user_id=payload.user_id, request_id=payload.request_id)
    except SoldOutError:
        raise HTTPException(status_code=409, detail="SOLD_OUT")
    except NoActiveSaleError:
        raise HTTPException(status_code=404, detail="NO_ACTIVE_SALE")
    return BuyResponse(ticket_number=result.ticket.ticket_number, sale_id=result.sale.id)


@router.get("/status", response_model=StatusResponse)
def status(db: Session = Depends(get_db)):
    service = SaleService(db)
    try:
        sale, sold_tickets = service.get_status()
    except NoActiveSaleError:
        raise HTTPException(status_code=404, detail="NO_ACTIVE_SALE")
    return StatusResponse(
        sale_id=sale.id,
        ticket_total=sale.ticket_total,
        sold_count=len(sold_tickets),
        sold_tickets=[
            SoldTicket(ticket_number=t.ticket_number, user_id=t.user_id, sold_at=t.sold_at)
            for t in sold_tickets
        ],
    )
