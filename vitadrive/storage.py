"""Persistence: the vehicle's permanent record of readings, alerts and services.

Works with SQLite (local/dev, pass a file path) and PostgreSQL (production, e.g. Supabase;
pass a ``postgresql://`` URL).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from .models import Alert, FuelLog, SensorReading, ServiceRecord, Trip, Vehicle

metadata = MetaData()

vehicles_t = Table(
    "vehicles", metadata,
    Column("vin", String(17), primary_key=True),
    Column("owner_id", String(255), nullable=False, index=True),
    Column("api_key_hash", String(64), nullable=True, index=True),
    Column("data", Text, nullable=False),
)
readings_t = Table(
    "readings", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String(17), ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False),
    Column("timestamp", String(40), nullable=False),
    Column("odometer_km", Float, nullable=False),
    Column("data", Text, nullable=False),
    Index("idx_readings_vin_ts", "vin", "timestamp"),
)
alerts_t = Table(
    "alerts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String(17), ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False),
    Column("timestamp", String(40), nullable=False),
    Column("metric", String(64), nullable=False),
    Column("severity", String(16), nullable=False),
    Column("acknowledged", Boolean, nullable=False, default=False),
    Column("data", Text, nullable=False),
    Index("idx_alerts_vin_ts", "vin", "timestamp"),
)
services_t = Table(
    "services", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String(17), ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False),
    Column("item", String(64), nullable=False),
    Column("performed_on", String(10), nullable=False),
    Column("odometer_km", Float, nullable=False),
    Column("data", Text, nullable=False),
)
fuel_logs_t = Table(
    "fuel_logs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String(17), ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False, index=True),
    Column("filled_on", String(10), nullable=False),
    Column("odometer_km", Float, nullable=False),
    Column("data", Text, nullable=False),
)
push_subs_t = Table(
    "push_subscriptions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner_id", String(255), nullable=False, index=True),
    Column("endpoint", String(2000), nullable=False, unique=True),
    Column("data", Text, nullable=False),
)
rate_limits_t = Table(
    "rate_limits", metadata,
    Column("key", String(255), primary_key=True),
    Column("window_start", Integer, primary_key=True),
    Column("count", Integer, nullable=False),
)
readings_daily_t = Table(
    "readings_daily", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String(17), ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False),
    Column("day", String(10), nullable=False),
    Column("odometer_km", Float, nullable=False),
    Column("count", Integer, nullable=False),
    Column("data", Text, nullable=False),
    UniqueConstraint("vin", "day", name="uq_readings_daily_vin_day"),
)
trips_t = Table(
    "trips", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("vin", String(17), ForeignKey("vehicles.vin", ondelete="CASCADE"), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("ended_at", String(40), nullable=False),
    Column("start_odometer_km", Float, nullable=False),
    Column("end_odometer_km", Float, nullable=False),
    Column("start_level_pct", Float, nullable=True),
    Column("end_level_pct", Float, nullable=True),
    Column("max_speed_kmh", Float, nullable=False),
    Column("samples", Integer, nullable=False),
    Index("idx_trips_vin_started_at", "vin", "started_at"),
)

LOCAL_OWNER = "local"


def database_url(target: str | Path) -> str:
    """Accept a SQLite path or a Postgres URL (as given by Supabase) and return a SQLAlchemy URL."""
    target = str(target)
    for prefix in ("postgres://", "postgresql://"):
        if target.startswith(prefix):
            return "postgresql+psycopg://" + target[len(prefix):]
    if "://" in target:
        return target
    return f"sqlite:///{target}"


def _create_engine(target: str | Path) -> Engine:
    url = database_url(target)
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys = ON"))
        return engine
    connect_args: dict[str, Any] = {"prepare_threshold": None}
    if "connect_timeout" not in make_url(url).query:
        connect_args["connect_timeout"] = 5
    return create_engine(url, poolclass=NullPool, connect_args=connect_args)


def _alembic_config(connection: Connection) -> Any:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    return config


def _lock_migrations(connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                           {"lock_id": 0x5649544144524956})


def upgrade_db(engine: Engine, revision: str = "head") -> None:
    """Apply migrations, safely adopting databases created before Alembic was used."""
    from alembic import command

    app_tables = {"vehicles", "readings", "alerts", "services", "fuel_logs", "push_subscriptions"}
    with engine.begin() as connection:
        _lock_migrations(connection)
        tables = set(inspect(connection).get_table_names())
        config = _alembic_config(connection)
        if "alembic_version" not in tables and "vehicles" in tables:
            missing = sorted(app_tables - tables)
            if missing:
                raise RuntimeError(
                    "Cannot migrate a partial legacy database: found 'vehicles' but missing "
                    f"application tables: {', '.join(missing)}."
                )
            command.stamp(config, "0001")
        command.upgrade(config, revision)


def stamp_db(engine: Engine, revision: str) -> None:
    from alembic import command

    with engine.begin() as connection:
        _lock_migrations(connection)
        command.stamp(_alembic_config(connection), revision)


def current_db_revision(engine: Engine) -> str | None:
    from sqlalchemy import inspect

    with engine.connect() as connection:
        if "alembic_version" not in inspect(connection).get_table_names():
            return None
        return cast(str | None, connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none())


def _auto_migrate_enabled() -> bool:
    return os.environ.get("VITADRIVE_AUTO_MIGRATE", "1").strip().lower() not in {"0", "false"}


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, target: str | Path = "vitadrive.db") -> None:
        self.engine = _create_engine(target)
        try:
            if _auto_migrate_enabled():
                upgrade_db(self.engine)
        except Exception:
            self.engine.dispose()
            raise
        self._local = threading.local()

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """Group several operations into a single database transaction."""
        existing = getattr(self._local, "conn", None)
        if existing is not None:
            yield existing
            return
        with self.engine.begin() as conn:
            self._local.conn = conn
            try:
                yield conn
            finally:
                self._local.conn = None

    # vehicles
    def upsert_vehicle(self, vehicle: Vehicle, owner_id: str = LOCAL_OWNER) -> Vehicle:
        with self.transaction() as c:
            exists = c.execute(select(vehicles_t.c.vin).where(vehicles_t.c.vin == vehicle.vin)).first()
            if exists:
                c.execute(update(vehicles_t).where(vehicles_t.c.vin == vehicle.vin)
                          .values(data=vehicle.model_dump_json()))
            else:
                c.execute(insert(vehicles_t).values(vin=vehicle.vin, owner_id=owner_id,
                                                    data=vehicle.model_dump_json()))
        return vehicle

    def get_vehicle(self, vin: str) -> Vehicle | None:
        with self.transaction() as c:
            row = c.execute(select(vehicles_t.c.data).where(vehicles_t.c.vin == vin)).first()
        return Vehicle.model_validate_json(row[0]) if row else None

    def vehicle_owner(self, vin: str) -> str | None:
        with self.transaction() as c:
            row = c.execute(select(vehicles_t.c.owner_id).where(vehicles_t.c.vin == vin)).first()
        return row[0] if row else None

    def list_vehicles(self, owner_id: str | None = None) -> list[Vehicle]:
        q = select(vehicles_t.c.data).order_by(vehicles_t.c.vin)
        if owner_id is not None:
            q = q.where(vehicles_t.c.owner_id == owner_id)
        with self.transaction() as c:
            return [Vehicle.model_validate_json(r[0]) for r in c.execute(q)]

    def delete_vehicle(self, vin: str) -> None:
        with self.transaction() as c:
            for t in (readings_t, readings_daily_t, trips_t, alerts_t, services_t, fuel_logs_t):
                c.execute(t.delete().where(t.c.vin == vin))
            c.execute(vehicles_t.delete().where(vehicles_t.c.vin == vin))

    def rotate_api_key(self, vin: str) -> str:
        """Issue a new ingest API key for a vehicle. Only the hash is stored; the key is shown once."""
        key = "vd_" + secrets.token_urlsafe(32)
        with self.transaction() as c:
            c.execute(update(vehicles_t).where(vehicles_t.c.vin == vin).values(api_key_hash=hash_api_key(key)))
        return key

    def vin_for_api_key(self, key: str) -> str | None:
        with self.transaction() as c:
            row = c.execute(select(vehicles_t.c.vin).where(vehicles_t.c.api_key_hash == hash_api_key(key))).first()
        return row[0] if row else None

    # readings
    def add_reading(self, reading: SensorReading) -> None:
        with self.transaction() as c:
            c.execute(insert(readings_t).values(vin=reading.vin, timestamp=_iso(reading.timestamp),
                                                odometer_km=reading.odometer_km, data=reading.model_dump_json()))

    def readings(self, vin: str, since: datetime | None = None, limit: int | None = None) -> list[SensorReading]:
        q = select(readings_t.c.data).where(readings_t.c.vin == vin)
        if since:
            q = q.where(readings_t.c.timestamp >= _iso(since))
        q = q.order_by(readings_t.c.timestamp.desc(), readings_t.c.id.desc())
        if limit:
            q = q.limit(limit)
        with self.transaction() as c:
            rows = c.execute(q).all()
            readings = [SensorReading.model_validate_json(r[0]) for r in rows]
            if limit is None or len(rows) < limit:
                summaries = select(readings_daily_t.c.data, readings_daily_t.c.count).where(
                    readings_daily_t.c.vin == vin
                )
                if since:
                    summaries = summaries.where(readings_daily_t.c.day >= _iso(since)[:10])
                summaries = summaries.order_by(readings_daily_t.c.day.desc())
                if limit is not None and since is None:
                    summaries = summaries.limit(max(0, limit - len(rows)))
                for row in c.execute(summaries):
                    reading = SensorReading.model_validate_json(row._mapping["data"])
                    reading._sample_count = row._mapping["count"]
                    readings.append(reading)
        if since:
            cutoff = _iso(since)
            readings = [reading for reading in readings if _iso(reading.timestamp) >= cutoff]
        readings.sort(key=lambda reading: _iso(reading.timestamp))
        if limit and len(readings) > limit:
            readings = readings[-limit:]
        return readings

    def latest_reading(self, vin: str) -> SensorReading | None:
        r = self.readings(vin, limit=1)
        return r[0] if r else None

    # trips
    def add_trip(self, trip: Trip) -> Trip:
        with self.transaction() as c:
            result = c.execute(insert(trips_t).values(
                vin=trip.vin,
                started_at=_iso(trip.started_at),
                ended_at=_iso(trip.ended_at),
                start_odometer_km=trip.start_odometer_km,
                end_odometer_km=trip.end_odometer_km,
                start_level_pct=trip.start_level_pct,
                end_level_pct=trip.end_level_pct,
                max_speed_kmh=trip.max_speed_kmh,
                samples=trip.samples,
            ))
        return trip.model_copy(update={"id": cast(tuple[Any, ...], result.inserted_primary_key)[0]})

    def update_trip(self, trip: Trip) -> None:
        if trip.id is None:
            raise ValueError("Cannot update a trip without an ID")
        with self.transaction() as c:
            c.execute(update(trips_t).where(
                trips_t.c.id == trip.id,
                trips_t.c.vin == trip.vin,
            ).values(
                started_at=_iso(trip.started_at),
                ended_at=_iso(trip.ended_at),
                start_odometer_km=trip.start_odometer_km,
                end_odometer_km=trip.end_odometer_km,
                start_level_pct=trip.start_level_pct,
                end_level_pct=trip.end_level_pct,
                max_speed_kmh=trip.max_speed_kmh,
                samples=trip.samples,
            ))

    def latest_trip(self, vin: str) -> Trip | None:
        trips = self.trips(vin, limit=1)
        return trips[0] if trips else None

    def trips(
        self,
        vin: str,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Trip]:
        q = select(trips_t).where(trips_t.c.vin == vin)
        if since is not None:
            q = q.where(trips_t.c.started_at >= _iso(since))
        q = q.order_by(trips_t.c.started_at.desc(), trips_t.c.id.desc())
        if limit is not None:
            q = q.limit(limit)
        with self.transaction() as c:
            rows = c.execute(q).all()
        return [Trip.model_validate(dict(row._mapping)) for row in rows]

    # alerts
    def add_alert(self, alert: Alert) -> Alert:
        with self.transaction() as c:
            result = c.execute(insert(alerts_t).values(
                vin=alert.vin, timestamp=_iso(alert.timestamp), metric=alert.metric,
                severity=alert.severity.value, acknowledged=alert.acknowledged,
                data=alert.model_dump_json(exclude={"id"}),
            ))
        return alert.model_copy(update={"id": cast(tuple[Any, ...], result.inserted_primary_key)[0]})

    def alerts(self, vin: str, include_acknowledged: bool = True, limit: int = 200) -> list[Alert]:
        q = select(alerts_t.c.id, alerts_t.c.acknowledged, alerts_t.c.data).where(alerts_t.c.vin == vin)
        if not include_acknowledged:
            q = q.where(alerts_t.c.acknowledged.is_(False))
        q = q.order_by(alerts_t.c.timestamp.desc(), alerts_t.c.id.desc()).limit(limit)
        with self.transaction() as c:
            rows = c.execute(q).all()
        return [Alert.model_validate_json(d).model_copy(update={"id": i, "acknowledged": bool(a)}) for i, a, d in rows]

    def has_open_alert(self, vin: str, metric: str, severity: str, value: str | None = None) -> bool:
        for alert in self.alerts(vin, include_acknowledged=False, limit=1000):
            if alert.metric == metric and alert.severity.value == severity and (
                    value is None or str(alert.value) == value):
                return True
        return False

    def alert_vin(self, alert_id: int) -> str | None:
        with self.transaction() as c:
            row = c.execute(select(alerts_t.c.vin).where(alerts_t.c.id == alert_id)).first()
        return row[0] if row else None

    def acknowledge_alert(self, alert_id: int) -> bool:
        with self.transaction() as c:
            result = c.execute(update(alerts_t).where(alerts_t.c.id == alert_id).values(acknowledged=True))
        return result.rowcount > 0

    # services
    def add_service(self, record: ServiceRecord) -> ServiceRecord:
        with self.transaction() as c:
            result = c.execute(insert(services_t).values(
                vin=record.vin, item=record.item, performed_on=record.performed_on.isoformat(),
                odometer_km=record.odometer_km, data=record.model_dump_json(exclude={"id"}),
            ))
        return record.model_copy(update={"id": cast(tuple[Any, ...], result.inserted_primary_key)[0]})

    def add_fuel_log(self, log: FuelLog) -> FuelLog:
        with self.transaction() as c:
            result = c.execute(insert(fuel_logs_t).values(
                vin=log.vin, filled_on=log.filled_on.isoformat(), odometer_km=log.odometer_km,
                data=log.model_dump_json(exclude={"id"}),
            ))
        return log.model_copy(update={"id": cast(tuple[Any, ...], result.inserted_primary_key)[0]})

    def fuel_logs(self, vin: str) -> list[FuelLog]:
        q = (select(fuel_logs_t.c.id, fuel_logs_t.c.data).where(fuel_logs_t.c.vin == vin)
             .order_by(fuel_logs_t.c.filled_on.desc(), fuel_logs_t.c.odometer_km.desc(), fuel_logs_t.c.id.desc()))
        with self.transaction() as c:
            rows = c.execute(q).all()
        return [FuelLog.model_validate_json(d).model_copy(update={"id": i}) for i, d in rows]

    def delete_fuel_log(self, vin: str, log_id: int) -> bool:
        with self.transaction() as c:
            q = fuel_logs_t.delete().where(fuel_logs_t.c.id == log_id, fuel_logs_t.c.vin == vin)
            return c.execute(q).rowcount > 0

    # push subscriptions (one row per browser/device that turned alerts on)
    def save_push_subscription(self, owner_id: str, subscription: dict[str, Any]) -> None:
        with self.transaction() as c:
            c.execute(push_subs_t.delete().where(push_subs_t.c.endpoint == subscription["endpoint"]))
            c.execute(insert(push_subs_t).values(owner_id=owner_id, endpoint=subscription["endpoint"],
                                                 data=json.dumps(subscription)))

    def push_subscriptions(self, owner_id: str) -> list[dict[str, Any]]:
        with self.transaction() as c:
            rows = c.execute(select(push_subs_t.c.data).where(push_subs_t.c.owner_id == owner_id)).all()
        return [json.loads(r[0]) for r in rows]

    def delete_push_subscription(self, endpoint: str, owner_id: str | None = None) -> bool:
        q = push_subs_t.delete().where(push_subs_t.c.endpoint == endpoint)
        if owner_id is not None:
            q = q.where(push_subs_t.c.owner_id == owner_id)
        with self.transaction() as c:
            return c.execute(q).rowcount > 0

    def services(self, vin: str) -> list[ServiceRecord]:
        q = (select(services_t.c.id, services_t.c.data).where(services_t.c.vin == vin)
             .order_by(services_t.c.performed_on.desc(), services_t.c.id.desc()))
        with self.transaction() as c:
            rows = c.execute(q).all()
        return [ServiceRecord.model_validate_json(d).model_copy(update={"id": i}) for i, d in rows]
