"""
Database engine / session management.

Version 1 (Naive) note:
We use the ordinary default isolation level (READ COMMITTED, Postgres's
default) and we do NOT set SERIALIZABLE, and we do NOT take any row locks
anywhere in this codebase. That is intentional -- see service.py for where
the race condition lives.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, echo=settings.echo_sql, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
