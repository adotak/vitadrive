"""Ties monitoring, persistence, maintenance planning and range estimation together."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel

from .anomaly import AnomalyResult
from .anomaly import score as anomaly_score
from .maintenance import MaintenancePlanner
from .models import Alert, MaintenanceForecast, RangeEstimate, SensorReading, Severity, Vehicle
from .monitor import HealthMonitor, overall_status
from .range_estimator import estimate_range
from .storage import Store


class UnknownVehicle(KeyError):
    pass


class HealthReport(BaseModel):
    vehicle: Vehicle
    status: Severity
    latest_reading: Optional[SensorReading]
    active_alerts: list[Alert]
    maintenance: list[MaintenanceForecast]
    range: Optional[RangeEstimate]
    anomaly: Optional[AnomalyResult] = None


class VitaDrive:
    def __init__(self, store: Store, monitor: Optional[HealthMonitor] = None,
                 planner: Optional[MaintenancePlanner] = None):
        self.store = store
        self.monitor = monitor or HealthMonitor()
        self.planner = planner or MaintenancePlanner()

    def _vehicle(self, vin: str) -> Vehicle:
        vehicle = self.store.get_vehicle(vin)
        if vehicle is None:
            raise UnknownVehicle(vin)
        return vehicle

    def ingest(self, reading: SensorReading) -> list[Alert]:
        """Record a reading and raise new alerts. Repeated alerts for an unacknowledged
        condition at the same severity are not duplicated."""
        with self.store.transaction():
            return self._ingest(reading)

    def _ingest(self, reading: SensorReading) -> list[Alert]:
        vehicle = self._vehicle(reading.vin)
        self.store.add_reading(reading)
        current = self.monitor.evaluate(reading, vehicle.powertrain)
        current_severity = {a.metric: a.severity for a in current if a.metric != "dtc"}
        # Auto-resolve sensor alerts whose condition cleared or changed severity. DTCs stay until acknowledged.
        for open_alert in self.store.alerts(reading.vin, include_acknowledged=False, limit=1000):
            if open_alert.metric != "dtc" and current_severity.get(open_alert.metric) is not open_alert.severity:
                self.store.acknowledge_alert(open_alert.id)
        raised = []
        for alert in current:
            dedupe_value = str(alert.value) if alert.metric == "dtc" else None
            if self.store.has_open_alert(reading.vin, alert.metric, alert.severity.value, dedupe_value):
                continue
            raised.append(self.store.add_alert(alert))
        return raised

    def maintenance(self, vin: str, today: Optional[date] = None) -> list[MaintenanceForecast]:
        vehicle = self._vehicle(vin)
        return self.planner.forecast(vehicle.powertrain, self.store.readings(vin),
                                     self.store.services(vin), today=today)

    def range(self, vin: str) -> Optional[RangeEstimate]:
        return estimate_range(self._vehicle(vin), self.store.readings(vin, limit=500))

    def report(self, vin: str, today: Optional[date] = None) -> HealthReport:
        vehicle = self._vehicle(vin)
        latest = self.store.latest_reading(vin)
        current = self.monitor.evaluate(latest, vehicle.powertrain) if latest else []
        active = self.store.alerts(vin, include_acknowledged=False, limit=50)
        maintenance = self.maintenance(vin, today=today)
        rng = self.range(vin)
        statuses = [overall_status(current + active)] + [m.status for m in maintenance]
        if rng:
            statuses.append(rng.status)
        detected = anomaly_score(latest) if latest else None
        if detected and detected.anomalous:
            statuses.append(Severity.INFO)
        order = [Severity.OK, Severity.INFO, Severity.WARNING, Severity.CRITICAL]
        status = max(statuses, key=order.index)
        return HealthReport(vehicle=vehicle, status=status, latest_reading=latest,
                            active_alerts=active, maintenance=maintenance, range=rng,
                            anomaly=detected)
