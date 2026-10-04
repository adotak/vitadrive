"""Maintenance schedule and due-date forecasting.

Each item is due by distance or time, whichever comes first. The distance-based
due date is projected using the vehicle's observed average daily distance. For
wear items with a live sensor (brake pads, tire tread, oil life) the wear rate
is fitted from the reading history and used to predict when the replacement
limit will be reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .models import MaintenanceForecast, Powertrain, SensorReading, ServiceRecord, Severity

_ALL = (Powertrain.ICE, Powertrain.HYBRID, Powertrain.EV)
_COMBUSTION = (Powertrain.ICE, Powertrain.HYBRID)

DEFAULT_DAILY_KM = 40.0
DUE_SOON_DAYS = 30
DUE_SOON_KM = 1000


@dataclass(frozen=True)
class MaintenanceItem:
    key: str
    label: str
    interval_km: Optional[float]
    interval_months: Optional[int]
    applies_to: tuple[Powertrain, ...] = _ALL
    wear_metric: Optional[str] = None
    wear_limit: Optional[float] = None


DEFAULT_SCHEDULE: tuple[MaintenanceItem, ...] = (
    MaintenanceItem("oil_change", "Engine oil & filter", 10_000, 12, _COMBUSTION,
                    wear_metric="engine_oil_life_pct", wear_limit=0),
    MaintenanceItem("tire_rotation", "Tire rotation", 10_000, 6),
    MaintenanceItem("brake_inspection", "Brake inspection", 20_000, 12),
    MaintenanceItem("brake_pads_front", "Front brake pads", 50_000, None,
                    wear_metric="brake_pad_front_mm", wear_limit=3.0),
    MaintenanceItem("brake_pads_rear", "Rear brake pads", 70_000, None,
                    wear_metric="brake_pad_rear_mm", wear_limit=3.0),
    MaintenanceItem("tires", "Tire replacement", 60_000, 72,
                    wear_metric="tire_tread_mm", wear_limit=1.6),
    MaintenanceItem("brake_fluid", "Brake fluid flush", None, 24),
    MaintenanceItem("cabin_filter", "Cabin air filter", 20_000, 12),
    MaintenanceItem("air_filter", "Engine air filter", 30_000, 24, _COMBUSTION),
    MaintenanceItem("coolant", "Coolant flush", 100_000, 60),
    MaintenanceItem("spark_plugs", "Spark plugs", 100_000, None, _COMBUSTION),
    MaintenanceItem("transmission_fluid", "Transmission fluid", 60_000, 48, _COMBUSTION),
    MaintenanceItem("battery_12v", "12V battery replacement", None, 48),
    MaintenanceItem("hv_battery_check", "Traction battery health check", 40_000, 24,
                    (Powertrain.HYBRID, Powertrain.EV)),
    MaintenanceItem("wiper_blades", "Wiper blades", None, 12),
)


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    days_in_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, days_in_month))


def average_daily_km(readings: list[SensorReading]) -> float:
    """Average distance driven per day, from the odometer history."""
    if len(readings) < 2:
        return DEFAULT_DAILY_KM
    ordered = sorted(readings, key=lambda r: r.timestamp)
    days = (ordered[-1].timestamp - ordered[0].timestamp).total_seconds() / 86400
    km = ordered[-1].odometer_km - ordered[0].odometer_km
    if days < 1 or km <= 0:
        return DEFAULT_DAILY_KM
    return km / days


def wear_rate_per_km(readings: list[SensorReading], metric: str) -> Optional[float]:
    """Least-squares slope of ``metric`` vs odometer (units per km). Negative means wearing."""
    points = [(r.odometer_km, getattr(r, metric)) for r in readings if getattr(r, metric, None) is not None]
    if len(points) < 2:
        return None
    n = len(points)
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    var_x = sum((p[0] - mean_x) ** 2 for p in points)
    if var_x == 0:
        return None
    slope = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points) / var_x
    return slope if slope < 0 else None


class MaintenancePlanner:
    def __init__(self, schedule: tuple[MaintenanceItem, ...] = DEFAULT_SCHEDULE):
        self.schedule = schedule

    def forecast(
        self,
        powertrain: Powertrain,
        readings: list[SensorReading],
        services: list[ServiceRecord],
        today: Optional[date] = None,
    ) -> list[MaintenanceForecast]:
        if not readings:
            return []
        today = today or date.today()
        ordered = sorted(readings, key=lambda r: r.timestamp)
        first, latest = ordered[0], ordered[-1]
        current_km = latest.odometer_km
        daily_km = average_daily_km(ordered)

        results = []
        for item in self.schedule:
            if powertrain not in item.applies_to:
                continue
            last = max((s for s in services if s.item == item.key),
                       key=lambda s: (s.performed_on, s.odometer_km), default=None)
            base_date = last.performed_on if last else first.timestamp.date()
            base_km = last.odometer_km if last else first.odometer_km
            readings_since = [r for r in ordered if r.odometer_km >= base_km]

            candidates: list[tuple[date, Optional[float], str]] = []
            if item.interval_months:
                candidates.append((_add_months(base_date, item.interval_months), None,
                                   f"every {item.interval_months} months"))
            if item.interval_km:
                due_km = base_km + item.interval_km
                km_left = due_km - current_km
                candidates.append((today + timedelta(days=max(km_left, 0) / daily_km), due_km,
                                   f"every {item.interval_km:,.0f} km"))
            wear = self._wear_prediction(item, readings_since, latest, current_km, daily_km, today)
            if wear:
                candidates.append(wear)
            if not candidates:
                continue

            due_date, due_km, reason = min(candidates, key=lambda c: c[0])
            if due_km is None:
                due_km = next((c[1] for c in candidates if c[1] is not None), None)
            km_remaining = None if due_km is None else round(due_km - current_km, 1)
            days_remaining = (due_date - today).days

            at_wear_limit = wear is not None and wear[0] == today and "at limit" in wear[2]
            if at_wear_limit or days_remaining < 0 or (km_remaining is not None and km_remaining < 0):
                status = Severity.CRITICAL
            elif days_remaining <= DUE_SOON_DAYS or (km_remaining is not None and km_remaining <= DUE_SOON_KM):
                status = Severity.WARNING
            else:
                status = Severity.OK

            results.append(MaintenanceForecast(
                item=item.key,
                label=item.label,
                last_service_date=last.performed_on if last else None,
                last_service_km=last.odometer_km if last else None,
                due_km=None if due_km is None else round(due_km, 1),
                due_date=due_date,
                days_remaining=days_remaining,
                km_remaining=km_remaining,
                status=status,
                reason=reason,
            ))
        return sorted(results, key=lambda f: f.due_date)

    @staticmethod
    def _wear_prediction(item, readings_since, latest, current_km, daily_km, today):
        if not item.wear_metric:
            return None
        value = getattr(latest, item.wear_metric, None)
        if value is None:
            return None
        if value <= item.wear_limit:
            return today, current_km, f"{item.wear_metric} at limit ({value:g})"
        rate = wear_rate_per_km(readings_since, item.wear_metric)
        if rate is None:
            return None
        km_left = (value - item.wear_limit) / -rate
        return (today + timedelta(days=km_left / daily_km), current_km + km_left,
                f"predicted from sensor wear rate ({item.wear_metric})")
