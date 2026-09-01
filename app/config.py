"""
Configuration for Ticket Stampede.

Version 1 (Naive): configuration only. No concurrency-related settings
exist here on purpose -- there is nothing to tune because there is no
protection yet.
"""
import os
from functools import lru_cache


class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://stampede:stampede@localhost:5432/stampede",
    )
    echo_sql: bool = os.getenv("ECHO_SQL", "false").lower() == "true"


@lru_cache
def get_settings() -> Settings:
    return Settings()
