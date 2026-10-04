"""SQLite persistence: the vehicle's permanent record of readings, alerts and services."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Alert, SensorReading, ServiceRecord, Vehicle

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    vin TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vin TEXT NOT NULL REFERENCES vehicles(vin),
    timestamp TEXT NOT NULL,
    odometer_km REAL NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_vin_ts ON readings(vin, timestamp);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vin TEXT NOT NULL REFERENCES vehicles(vin),
    timestamp TEXT NOT NULL,
    metric TEXT NOT NULL,
    severity TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_vin_ts ON alerts(vin, timestamp);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vin TEXT NOT NULL REFERENCES vehicles(vin),
    item TEXT NOT NULL,
    performed_on TEXT NOT NULL,
    odometer_km REAL NOT NULL,
    data TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path = "vitadrive.db"):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    # vehicles
    def upsert_vehicle(self, vehicle: Vehicle) -> Vehicle:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO vehicles(vin, data) VALUES (?, ?) ON CONFLICT(vin) DO UPDATE SET data=excluded.data",
                (vehicle.vin, vehicle.model_dump_json()),
            )
        return vehicle

    def get_vehicle(self, vin: str) -> Optional[Vehicle]:
        row = self._conn.execute("SELECT data FROM vehicles WHERE vin=?", (vin,)).fetchone()
        return Vehicle.model_validate_json(row[0]) if row else None

    def list_vehicles(self) -> list[Vehicle]:
        return [Vehicle.model_validate_json(r[0]) for r in self._conn.execute("SELECT data FROM vehicles ORDER BY vin")]

    # readings
    def add_reading(self, reading: SensorReading) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO readings(vin, timestamp, odometer_km, data) VALUES (?, ?, ?, ?)",
                (reading.vin, reading.timestamp.isoformat(), reading.odometer_km, reading.model_dump_json()),
            )

    def readings(self, vin: str, since: Optional[datetime] = None, limit: Optional[int] = None) -> list[SensorReading]:
        sql = "SELECT data FROM readings WHERE vin=?"
        params: list = [vin]
        if since:
            sql += " AND timestamp >= ?"
            params.append(since.isoformat())
        sql += " ORDER BY timestamp DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [SensorReading.model_validate_json(r[0]) for r in reversed(rows)]

    def latest_reading(self, vin: str) -> Optional[SensorReading]:
        r = self.readings(vin, limit=1)
        return r[0] if r else None

    # alerts
    def add_alert(self, alert: Alert) -> Alert:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO alerts(vin, timestamp, metric, severity, acknowledged, data) VALUES (?, ?, ?, ?, ?, ?)",
                (alert.vin, alert.timestamp.isoformat(), alert.metric, alert.severity.value,
                 int(alert.acknowledged), alert.model_dump_json(exclude={"id"})),
            )
        return alert.model_copy(update={"id": cur.lastrowid})

    def alerts(self, vin: str, include_acknowledged: bool = True, limit: int = 200) -> list[Alert]:
        sql = "SELECT id, acknowledged, data FROM alerts WHERE vin=?"
        if not include_acknowledged:
            sql += " AND acknowledged=0"
        sql += " ORDER BY timestamp DESC, id DESC LIMIT ?"
        return [Alert.model_validate_json(d).model_copy(update={"id": i, "acknowledged": bool(a)})
                for i, a, d in self._conn.execute(sql, (vin, limit))]

    def has_open_alert(self, vin: str, metric: str, severity: str, value: str | None = None) -> bool:
        for alert in self.alerts(vin, include_acknowledged=False, limit=1000):
            if alert.metric == metric and alert.severity.value == severity and (
                    value is None or str(alert.value) == value):
                return True
        return False

    def acknowledge_alert(self, alert_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))
        return cur.rowcount > 0

    # services
    def add_service(self, record: ServiceRecord) -> ServiceRecord:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO services(vin, item, performed_on, odometer_km, data) VALUES (?, ?, ?, ?, ?)",
                (record.vin, record.item, record.performed_on.isoformat(), record.odometer_km,
                 record.model_dump_json(exclude={"id"})),
            )
        return record.model_copy(update={"id": cur.lastrowid})

    def services(self, vin: str) -> list[ServiceRecord]:
        return [ServiceRecord.model_validate_json(d).model_copy(update={"id": i}) for i, d in self._conn.execute(
            "SELECT id, data FROM services WHERE vin=? ORDER BY performed_on DESC, id DESC", (vin,))]
