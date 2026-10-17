from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .storage import rate_limits_t


@dataclass(frozen=True)
class Limit:
    name: str
    max_hits: int
    window_s: int


class RateLimited(Exception):
    def __init__(self, retry_after: int, message: str = "Too many requests. Please try again later."):
        self.retry_after = max(1, int(retry_after))
        self.message = message
        super().__init__(message)


DEFAULT_LIMITS = {
    "demo": Limit("demo", max_hits=5, window_s=3600),
    "ask": Limit("ask", max_hits=10, window_s=60),
    "ask_daily": Limit("ask_daily", max_hits=50, window_s=86400),
    "readings": Limit("readings", max_hits=120, window_s=60),
    "nearby": Limit("nearby", max_hits=20, window_s=60),
    "api_key": Limit("api_key", max_hits=10, window_s=3600),
}


def default_limits() -> dict[str, Limit]:
    limits = DEFAULT_LIMITS.copy()
    limits["ask_daily"] = Limit(
        "ask_daily",
        max_hits=int(os.environ.get("ASK_DAILY_QUOTA", "50")),
        window_s=86400,
    )
    return limits


class RateLimiter:
    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock

    def hit(self, limit: Limit, subject: str) -> None:
        now = self.clock()
        window_start = int(now // limit.window_s * limit.window_s)
        key = f"{limit.name}:{subject}"
        dialect = self.store.engine.dialect.name
        if dialect == "sqlite":
            insert = sqlite_insert
        elif dialect == "postgresql":
            insert = postgresql_insert
        else:
            raise RuntimeError(f"Rate limiting is not supported for database dialect {dialect!r}")

        statement = insert(rate_limits_t).values(key=key, window_start=window_start, count=1)
        statement = statement.on_conflict_do_update(
            index_elements=[rate_limits_t.c.key, rate_limits_t.c.window_start],
            set_={"count": rate_limits_t.c.count + 1},
        ).returning(rate_limits_t.c.count)

        with self.store.transaction() as connection:
            count = connection.execute(statement).scalar_one()
            connection.execute(delete(rate_limits_t).where(
                rate_limits_t.c.key == key,
                rate_limits_t.c.window_start < window_start,
            ))

        if count > limit.max_hits:
            retry_after = max(1, math.ceil(window_start + limit.window_s - now))
            message = ("Daily Ask VitaDrive quota reached. It resets at midnight UTC."
                       if limit.name == "ask_daily"
                       else "Too many requests. Please try again later.")
            raise RateLimited(retry_after, message)
