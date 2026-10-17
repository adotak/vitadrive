"""Compact old raw readings into per-day summaries."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Connection, delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import SensorReading
from .storage import Store, rate_limits_t, readings_daily_t, readings_t, vehicles_t

_EXCLUDED_FIELDS = {"vin", "timestamp", "odometer_km", "dtc_codes"}
_RATE_LIMIT_MAX_WINDOW_S = 86400


class PruneResult(BaseModel):
    vehicles: int
    days_summarized: int
    readings_removed: int
    rate_limit_rows_removed: int


def _utc_timestamp(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _numeric_values(readings: list[SensorReading], field: str) -> list[float]:
    return [
        value for reading in readings
        if isinstance(value := getattr(reading, field), (int, float)) and not isinstance(value, bool)
    ]


def _latest_reading(readings: list[SensorReading]) -> SensorReading:
    if not readings:
        raise ValueError("Cannot summarize an empty list of readings")
    return max(readings, key=lambda reading: _utc_timestamp(reading.timestamp))


def summarize_day(readings: list[SensorReading]) -> SensorReading:
    """Summarize a day's readings using numeric means and the final timestamp/odometer."""
    if not readings:
        raise ValueError("Cannot summarize an empty list of readings")
    latest = _latest_reading(readings)
    values = {
        field: sum(field_values) / len(field_values) if (field_values := _numeric_values(readings, field)) else None
        for field in SensorReading.model_fields
        if field not in _EXCLUDED_FIELDS
    }
    return SensorReading(
        vin=latest.vin,
        timestamp=_utc_timestamp(latest.timestamp),
        odometer_km=latest.odometer_km,
        dtc_codes=sorted({code for reading in readings for code in reading.dtc_codes}),
        **values,
    )


def merge_summary(
    existing: SensorReading,
    existing_count: int,
    new: list[SensorReading],
) -> SensorReading:
    """Merge late readings into a summary, weighting its means by its raw-reading count."""
    if existing_count < 1:
        raise ValueError("existing_count must be at least 1")
    if not new:
        return existing
    if any(reading.vin != existing.vin for reading in new):
        raise ValueError("All readings in a summary must have the same VIN")

    latest = _latest_reading([existing, *new])
    values = {}
    for field in SensorReading.model_fields:
        if field in _EXCLUDED_FIELDS:
            continue
        previous = getattr(existing, field)
        additions = _numeric_values(new, field)
        if previous is None:
            values[field] = sum(additions) / len(additions) if additions else None
        elif additions:
            values[field] = (previous * existing_count + sum(additions)) / (existing_count + len(additions))
        else:
            values[field] = previous

    return SensorReading(
        vin=existing.vin,
        timestamp=_utc_timestamp(latest.timestamp),
        odometer_km=latest.odometer_km,
        dtc_codes=sorted({*existing.dtc_codes, *(code for reading in new for code in reading.dtc_codes)}),
        **values,
    )


def _cutoff(now: datetime, older_than_days: int) -> datetime:
    if older_than_days < 0:
        raise ValueError("older_than_days must be non-negative")
    cutoff_day = (_utc_timestamp(now) - timedelta(days=older_than_days)).date()
    return datetime.combine(cutoff_day, time.min, tzinfo=timezone.utc)


def _upsert_daily(connection: Connection, vin: str, day: str, summary: SensorReading, count: int) -> None:
    dialect = connection.dialect.name
    insert_statement: Any
    if dialect == "sqlite":
        insert_statement = sqlite_insert(readings_daily_t)
    elif dialect == "postgresql":
        insert_statement = postgresql_insert(readings_daily_t)
    else:
        raise RuntimeError(f"Daily reading retention is not supported for database dialect {dialect!r}")
    statement = insert_statement.values(
        vin=vin,
        day=day,
        odometer_km=summary.odometer_km,
        count=count,
        data=summary.model_dump_json(),
    ).on_conflict_do_update(
        index_elements=[readings_daily_t.c.vin, readings_daily_t.c.day],
        set_={
            "odometer_km": summary.odometer_km,
            "count": count,
            "data": summary.model_dump_json(),
        },
    )
    connection.execute(statement)


def prune(
    store: Store,
    older_than_days: int = 30,
    now: datetime | None = None,
    dry_run: bool = False,
) -> PruneResult:
    """Compact complete UTC days of old readings and remove expired rate-limit windows."""
    now = _utc_timestamp(now or datetime.now(timezone.utc))
    cutoff = _cutoff(now, older_than_days)
    cutoff_text = cutoff.isoformat()
    with store.transaction() as connection:
        vins = connection.execute(select(vehicles_t.c.vin)).scalars().all()

    vehicles = days_summarized = readings_removed = 0
    for vin in vins:
        with store.transaction() as connection:
            lock = select(vehicles_t.c.vin).where(vehicles_t.c.vin == vin)
            if connection.dialect.name == "postgresql":
                lock = lock.with_for_update()
            connection.execute(lock).first()
            rows = connection.execute(
                select(readings_t.c.id, readings_t.c.data)
                .where(readings_t.c.vin == vin, readings_t.c.timestamp < cutoff_text)
                .order_by(readings_t.c.timestamp, readings_t.c.id)
            ).all()
            groups: dict[str, list[SensorReading]] = defaultdict(list)
            row_ids = []
            for row_id, data in rows:
                reading = SensorReading.model_validate_json(data)
                day = _utc_timestamp(reading.timestamp).date().isoformat()
                groups[day].append(reading)
                row_ids.append(row_id)

            for day, new in groups.items():
                existing_row = connection.execute(
                    select(readings_daily_t.c.count, readings_daily_t.c.data).where(
                        readings_daily_t.c.vin == vin,
                        readings_daily_t.c.day == day,
                    )
                ).first()
                if existing_row:
                    summary = merge_summary(
                        SensorReading.model_validate_json(existing_row._mapping["data"]),
                        existing_row._mapping["count"],
                        new,
                    )
                    count = existing_row._mapping["count"] + len(new)
                else:
                    summary = summarize_day(new)
                    count = len(new)
                if not dry_run:
                    _upsert_daily(connection, vin, day, summary, count)
            if row_ids and not dry_run:
                connection.execute(delete(readings_t).where(readings_t.c.id.in_(row_ids)))
        if groups:
            vehicles += 1
            days_summarized += len(groups)
            readings_removed += len(row_ids)

    stale_window_start = int(now.timestamp()) - 2 * 86400 - _RATE_LIMIT_MAX_WINDOW_S
    with store.transaction() as connection:
        stale = select(func.count()).select_from(rate_limits_t).where(
            rate_limits_t.c.window_start < stale_window_start
        )
        rate_limit_rows_removed = connection.execute(stale).scalar_one()
        if not dry_run and rate_limit_rows_removed:
            connection.execute(delete(rate_limits_t).where(rate_limits_t.c.window_start < stale_window_start))

    return PruneResult(
        vehicles=vehicles,
        days_summarized=days_summarized,
        readings_removed=readings_removed,
        rate_limit_rows_removed=rate_limit_rows_removed,
    )
