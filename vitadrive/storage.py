"""Persistence: the vehicle's permanent record of readings, alerts and services.

Works with SQLite (local/dev, pass a file path) and PostgreSQL (production, e.g. Supabase;
pass a ``postgresql://`` URL).
"""
from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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
    create_engine,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.pool import NullPool

from .models import Alert, SensorReading, ServiceRecord, Vehicle

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


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, target: str | Path = "vitadrive.db"):
        url = database_url(target)
        if url.startswith("sqlite"):
            self.engine = create_engine(url, connect_args={"check_same_thread": False})
            event.listen(self.engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys = ON"))
        else:
            # Serverless-friendly: no client-side pool (use Supabase's pooler), and no server-side
            # prepared statements, which transaction-mode poolers don't support.
            self.engine = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})
        metadata.create_all(self.engine)
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
            for t in (readings_t, alerts_t, services_t):
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
        return [SensorReading.model_validate_json(r[0]) for r in reversed(rows)]

    def latest_reading(self, vin: str) -> SensorReading | None:
        r = self.readings(vin, limit=1)
        return r[0] if r else None

    # alerts
    def add_alert(self, alert: Alert) -> Alert:
        with self.transaction() as c:
            result = c.execute(insert(alerts_t).values(
                vin=alert.vin, timestamp=_iso(alert.timestamp), metric=alert.metric,
                severity=alert.severity.value, acknowledged=alert.acknowledged,
                data=alert.model_dump_json(exclude={"id"}),
            ))
        return alert.model_copy(update={"id": result.inserted_primary_key[0]})

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
        return record.model_copy(update={"id": result.inserted_primary_key[0]})

    def services(self, vin: str) -> list[ServiceRecord]:
        q = (select(services_t.c.id, services_t.c.data).where(services_t.c.vin == vin)
             .order_by(services_t.c.performed_on.desc(), services_t.c.id.desc()))
        with self.transaction() as c:
            rows = c.execute(q).all()
        return [ServiceRecord.model_validate_json(d).model_copy(update={"id": i}) for i, d in rows]
