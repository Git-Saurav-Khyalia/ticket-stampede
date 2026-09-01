"""
Test fixtures.

These tests exercise NORMAL SEQUENTIAL behavior only, as specified for
Version 1. They intentionally do NOT test concurrent /buy calls -- that
is the load tester's job, built separately in a later step, specifically
because it is expected to catch this version failing.

Tests run against a real Postgres instance (see docker-compose.yml).
Set TEST_DATABASE_URL to point at a scratch database; defaults to the
same DB as the app for local convenience.
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "DATABASE_URL",
    os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://stampede:stampede@localhost:5432/stampede",
    ),
)

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(TEST_DATABASE_URL, future=True)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    # Truncate between tests so each test starts from a blank slate.
    with engine.begin() as conn:
        conn.exec_driver_sql("TRUNCATE TABLE tickets, sales RESTART IDENTITY CASCADE")
    yield


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture
def client():
    return TestClient(app)
