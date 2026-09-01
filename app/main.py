from fastapi import FastAPI

from app.database import Base, engine
from app.routes import router

# Version 1 (Naive): create tables on startup for local/dev convenience.
# A real deployment would use migrations (Alembic); out of scope for this
# take-home per the time budget.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Ticket Stampede - Seller (Version 1: Naive)",
    description=(
        "INTENTIONALLY UNSAFE reference implementation. Contains a known "
        "race condition and a known idempotency gap. Do not deploy. "
        "See app/service.py for details."
    ),
    version="1.0.0-naive",
)

app.include_router(router)


@app.get("/")
def root():
    return {
        "service": "ticket-stampede-seller",
        "version": "1.0.0-naive",
        "warning": "Intentionally unsafe under concurrency. See README.",
    }
